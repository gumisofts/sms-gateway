<#
.SYNOPSIS
  Provision SMS Gateway phones over adb (install APK, grant permissions, raise the Android SMS limit,
  battery whitelist, stay awake while charging). Idempotent; one failing phone never stops the rest.

.PARAMETER Serial
  Provision only this device serial. Default: every device in `adb devices`.

.PARAMETER ApkPath
  APK to install. Default: android\app\build\outputs\apk\debug\app-debug.apk under the repo root.

.PARAMETER Adb
  Path to adb.exe if it is not on PATH (e.g. C:\Users\<user>\platform_tools\adb.exe).

.EXAMPLE
  .\scripts\provision-device.ps1
  .\scripts\provision-device.ps1 -Serial R58M123ABC -ApkPath C:\apks\app-debug.apk
#>
[CmdletBinding()]
param(
    [string]$Serial = "",
    [string]$ApkPath = "",
    [string]$Adb = "adb"
)

$ErrorActionPreference = "Continue"
$Pkg = "com.smsgateway.app"
$MaxCount = "100000"
$IntervalMs = "60000"
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $ApkPath) { $ApkPath = Join-Path $RepoRoot "android\app\build\outputs\apk\debug\app-debug.apk" }

if (-not (Get-Command $Adb -ErrorAction SilentlyContinue)) {
    Write-Error "adb not found. Add platform-tools to PATH or pass -Adb C:\path\to\adb.exe"
    exit 2
}
if (-not (Test-Path $ApkPath)) {
    Write-Error "APK not found: $ApkPath. Build it (cd android; .\gradlew.bat assembleDebug) or pass -ApkPath."
    exit 2
}

# Run adb for one device, return trimmed text output (stderr merged), never throws.
function Invoke-Dev([string]$s, [string[]]$AdbArgs) {
    try { $o = & $Adb -s $s @AdbArgs 2>&1 | Out-String } catch { $o = "" }
    return $o.Trim()
}

$lines = & $Adb devices 2>&1 | Select-Object -Skip 1 | Where-Object { $_ -match "\S" }
$serials = @()
foreach ($l in $lines) {
    $p = $l -split "\s+"
    if ($p.Count -ge 2 -and $p[1] -eq "device") { $serials += $p[0] }
    elseif ($p.Count -ge 2 -and $p[0] -notmatch "^\*") { Write-Warning "Skipping $($p[0]) (state: $($p[1])) - authorise USB debugging on the phone" }
}
if ($Serial) { $serials = @($Serial) }
if ($serials.Count -eq 0) {
    Write-Error "No devices in 'device' state. Check cable, USB debugging and the authorisation prompt."
    exit 2
}

$results = @()
foreach ($s in $serials) {
    try {
        $model = Invoke-Dev $s @("shell", "getprop", "ro.product.model")
        $sdkText = Invoke-Dev $s @("shell", "getprop", "ro.build.version.sdk")
        Write-Host "=== $s ($model, SDK $sdkText) ==="
        $sdk = 0
        if (-not [int]::TryParse($sdkText, [ref]$sdk)) {
            $results += [pscustomobject]@{ Result = "FAIL"; Serial = $s; Model = $model; Detail = "unreachable" }
            continue
        }

        Write-Host "  installing $ApkPath"
        $inst = Invoke-Dev $s @("install", "-r", $ApkPath)
        Write-Host "    $(($inst -split "`n")[-1])"

        $perms = @("android.permission.SEND_SMS", "android.permission.READ_PHONE_STATE", "android.permission.WRITE_SECURE_SETTINGS")
        if ($sdk -ge 33) { $perms += "android.permission.POST_NOTIFICATIONS" }
        foreach ($p in $perms) {
            $o = Invoke-Dev $s @("shell", "pm", "grant", $Pkg, $p)
            if ($o) { Write-Host "  pm grant $($p.Split('.')[-1]): $o" }
        }

        [void](Invoke-Dev $s @("shell", "settings", "put", "global", "sms_outgoing_check_max_count", $MaxCount))
        [void](Invoke-Dev $s @("shell", "settings", "put", "global", "sms_outgoing_check_interval_ms", $IntervalMs))
        [void](Invoke-Dev $s @("shell", "dumpsys", "deviceidle", "whitelist", "+$Pkg"))
        [void](Invoke-Dev $s @("shell", "settings", "put", "global", "stay_on_while_plugged_in", "3"))
        [void](Invoke-Dev $s @("shell", "settings", "put", "global", "wifi_sleep_policy", "2"))

        # ---- verify ----
        $fails = @()
        $pk = Invoke-Dev $s @("shell", "pm", "list", "packages", $Pkg)
        if ($pk -notmatch "(?m)^package:$([regex]::Escape($Pkg))\s*$") { $fails += "package-not-installed" }
        $dump = Invoke-Dev $s @("shell", "dumpsys", "package", $Pkg)
        foreach ($p in $perms) {
            if ($dump -notmatch ([regex]::Escape($p) + ": granted=true")) { $fails += $p.Split('.')[-1] }
        }
        $v = Invoke-Dev $s @("shell", "settings", "get", "global", "sms_outgoing_check_max_count")
        if ($v -ne $MaxCount) { $fails += "sms-limit(max_count=$v)" }
        $v = Invoke-Dev $s @("shell", "settings", "get", "global", "sms_outgoing_check_interval_ms")
        if ($v -ne $IntervalMs) { $fails += "sms-limit(interval=$v)" }
        $wl = Invoke-Dev $s @("shell", "dumpsys", "deviceidle", "whitelist")
        if ($wl -notmatch [regex]::Escape($Pkg)) { $fails += "battery-whitelist" }
        $v = Invoke-Dev $s @("shell", "settings", "get", "global", "stay_on_while_plugged_in")
        if ($v -ne "3") { $fails += "stay-awake" }

        if ($fails.Count -eq 0) { $r = "PASS"; $d = "" } else { $r = "FAIL"; $d = "missing: " + ($fails -join " ") }
        $results += [pscustomobject]@{ Result = $r; Serial = $s; Model = $model; Detail = $d }
    } catch {
        $results += [pscustomobject]@{ Result = "FAIL"; Serial = $s; Model = ""; Detail = "error: $($_.Exception.Message)" }
    }
}

Write-Host ""
Write-Host "===== SUMMARY ====="
$results | Format-Table -AutoSize | Out-String | Write-Host
Write-Host "Manual steps still needed per phone: OEM autostart / unrestricted battery, SIM PIN off."
Write-Host "See docs\DEVICE_SETUP.md. Xiaomi: enable 'USB debugging (Security settings)' if FAIL on grants/limit."
if ($results | Where-Object { $_.Result -eq "FAIL" }) { exit 1 } else { exit 0 }
