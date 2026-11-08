#!/usr/bin/env bash
# Provision SMS Gateway phones over adb: install APK, grant permissions, raise the
# Android SMS limit, whitelist from battery optimisation, keep awake while charging.
# Idempotent: safe to re-run. A failure on one device never stops the others.
#
# Usage: scripts/provision-device.sh [-s SERIAL] [APK_PATH]
#        scripts/provision-device.sh [SERIAL] [APK_PATH]   (serial = first arg if it is not a file)
# Env:   ADB=/path/to/adb   to use a specific adb binary.
set -u

PKG="com.smsgateway.app"
MAX_COUNT=100000
INTERVAL_MS=60000
ADB="${ADB:-adb}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APK="$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk"
SERIAL=""

while [ $# -gt 0 ]; do
  case "$1" in
    -s) SERIAL="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *)
      if [ -f "$1" ]; then APK="$1"; else SERIAL="$1"; fi
      shift ;;
  esac
done

if ! command -v "$ADB" >/dev/null 2>&1; then
  echo "adb not found. Install Android platform-tools or set ADB=/path/to/adb." >&2
  exit 2
fi
if [ ! -f "$APK" ]; then
  echo "APK not found: $APK" >&2
  echo "Build it (cd android && ./gradlew assembleDebug) or pass the path as an argument." >&2
  exit 2
fi

if [ -n "$SERIAL" ]; then
  SERIALS="$SERIAL"
else
  SERIALS="$("$ADB" devices | awk 'NR>1 && $2=="device" {print $1}')"
  "$ADB" devices | awk 'NR>1 && $2!="" && $2!="device" {print "SKIPPED " $1 " (state: " $2 " - authorise USB debugging on the phone)"}'
fi
if [ -z "$SERIALS" ]; then
  echo "No devices in 'device' state. Check cable, USB debugging and the authorisation prompt." >&2
  exit 2
fi

SUMMARY=""
FAILED=0

provision() {
  local s="$1" fails="" model sdk perms p out
  a() { "$ADB" -s "$s" "$@"; }
  model="$(a shell getprop ro.product.model 2>/dev/null | tr -d '\r')"
  sdk="$(a shell getprop ro.build.version.sdk 2>/dev/null | tr -d '\r')"
  echo "=== $s ($model, SDK $sdk) ==="
  case "$sdk" in ''|*[!0-9]*) echo "  cannot read SDK level"; SUMMARY+="FAIL  $s  unreachable"$'\n'; FAILED=1; return ;; esac

  echo "  installing $APK"
  a install -r "$APK" 2>&1 | tail -n 2 | sed 's/^/    /'

  perms="android.permission.SEND_SMS android.permission.READ_PHONE_STATE android.permission.WRITE_SECURE_SETTINGS"
  [ "$sdk" -ge 33 ] && perms="$perms android.permission.POST_NOTIFICATIONS"
  for p in $perms; do
    out="$(a shell pm grant "$PKG" "$p" 2>&1 | tr -d '\r')"
    [ -n "$out" ] && echo "  pm grant ${p##*.}: $out"
  done

  a shell settings put global sms_outgoing_check_max_count "$MAX_COUNT" >/dev/null 2>&1
  a shell settings put global sms_outgoing_check_interval_ms "$INTERVAL_MS" >/dev/null 2>&1
  a shell dumpsys deviceidle whitelist "+$PKG" >/dev/null 2>&1
  a shell settings put global stay_on_while_plugged_in 3 >/dev/null 2>&1
  a shell settings put global wifi_sleep_policy 2 >/dev/null 2>&1   # ignored on newer Android

  # ---- verify ----
  local installed dump v
  installed="$(a shell pm list packages "$PKG" 2>/dev/null | tr -d '\r' | grep -x "package:$PKG")"
  [ -n "$installed" ] || fails="$fails package-not-installed"
  dump="$(a shell dumpsys package "$PKG" 2>/dev/null | tr -d '\r')"
  for p in $perms; do
    echo "$dump" | grep -q "$p: granted=true" || fails="$fails ${p##*.}"
  done
  v="$(a shell settings get global sms_outgoing_check_max_count 2>/dev/null | tr -d '\r')"
  [ "$v" = "$MAX_COUNT" ] || fails="$fails sms-limit(max_count=$v)"
  v="$(a shell settings get global sms_outgoing_check_interval_ms 2>/dev/null | tr -d '\r')"
  [ "$v" = "$INTERVAL_MS" ] || fails="$fails sms-limit(interval=$v)"
  a shell dumpsys deviceidle whitelist 2>/dev/null | grep -q "$PKG" || fails="$fails battery-whitelist"
  v="$(a shell settings get global stay_on_while_plugged_in 2>/dev/null | tr -d '\r')"
  [ "$v" = "3" ] || fails="$fails stay-awake"

  if [ -z "$fails" ]; then
    SUMMARY+="PASS  $s  $model"$'\n'
  else
    SUMMARY+="FAIL  $s  $model  missing:$fails"$'\n'
    FAILED=1
  fi
}

for s in $SERIALS; do
  provision "$s" </dev/null || { SUMMARY+="FAIL  $s  error"$'\n'; FAILED=1; }
done

echo
echo "===== SUMMARY ====="
printf '%s' "$SUMMARY"
echo
echo "Manual steps still needed per phone: OEM autostart / unrestricted battery, SIM PIN off."
echo "See docs/DEVICE_SETUP.md. Xiaomi: enable 'USB debugging (Security settings)' if FAIL on grants/limit."
exit "$FAILED"
