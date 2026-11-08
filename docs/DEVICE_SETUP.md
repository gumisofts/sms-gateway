# Device setup guide

How to prepare, provision and verify Android phones as SMS gateway nodes (written for 5-50 phones).
Server: `https://sms.bita.et`. App id: `com.smsgateway.app`.

## 1. Hardware and SIM checklist

- One SIM-enabled Android phone per node, Android 8.0+ (min SDK 26). Dual-SIM phones send on both SIMs.
- Every SIM has airtime/bundle for SMS, is active, and has a PIN-free unlock (see 2.4).
- Power: original charger and cable, wall socket on a surge protector. Phones run 24/7 on charge.
- Heat: keep phones in the open, on a shelf with air around them. No cases, no stacking, no direct sun.
- Battery: check for swelling (screen lifting, back bulging) before deployment and monthly. Retire a
  swollen phone immediately. Phones that charge 24/7 wear batteries fast, so prefer a battery charge
  limit (Samsung "Protect battery", see 2.5) where available.
- Wi-Fi: use a stable network, one access point per ~20-25 phones, 2.4 GHz is fine. Give phones
  fixed DHCP leases if possible. Mobile data on the SIM can be the fallback but costs money.
- Spread phones across at least two sites/power circuits so one outage or one router failure does not
  stop everything.
- Label each phone (number, SIM carrier, serial) and keep a sheet with the same.

## 2. One-time phone preparation (per phone)

### 2.1 Developer options and USB debugging
1. Settings > About phone > tap **Build number** 7 times.
2. Settings > System > Developer options > enable **USB debugging**.
3. Xiaomi/Redmi/POCO: also enable **USB debugging (Security settings)** and **Install via USB**
   (requires a Mi account and a SIM/data). Without it, `pm grant` and `settings put` fail.
4. Optional: enable **Stay awake** in Developer options (the script also sets it).

### 2.2 Install permission
If the phone blocks the install, allow "Install unknown apps" for the app that opens the APK, or turn
off the Play Protect "Scan apps with Play Protect" prompt (Play Store > profile > Play Protect > settings).
`adb install` normally bypasses this, but some OEMs (Xiaomi, Oppo, Vivo) show a confirmation on screen.
Watch the phone during the first install.

### 2.3 Disable automatic system updates
Developer options > Automatic system updates: off (where present). An unattended reboot or update
can leave the phone sitting on a lock screen with a SIM PIN prompt.

### 2.4 Remove the SIM PIN
Settings > Security > SIM card lock > turn off "Lock SIM card". A PIN-locked SIM stays offline after
every reboot until someone types the PIN. Do this for both SIMs.

### 2.5 Battery and autostart settings (manual, per brand)
These cannot be scripted reliably. Do all of them: the OEM task killer is the number one cause of
phones going silent. Reference: https://dontkillmyapp.com. Menu names vary by version.

| Brand | Settings to change |
|-------|--------------------|
| Samsung (One UI) | Settings > Apps > SMS Gateway > Battery > **Unrestricted**. Settings > Battery > Background usage limits > **Never sleeping apps** > add SMS Gateway; turn off "Put unused apps to sleep" and "Adaptive battery". For phones on charge 24/7: Battery > More battery settings > **Protect battery** (85% limit). |
| Xiaomi / Redmi / POCO (MIUI / HyperOS) | Settings > Apps > Manage apps > SMS Gateway > **Autostart: on**; **Battery saver: No restrictions**. Security app > Battery > App battery saver > No restrictions. In Recents, long-press the app card and **lock** it. |
| Tecno / Infinix / itel (HiOS / XOS / itel OS) | **Phone Master** (or Phone Manager) > Battery/Power management > App launch management (auto-start) > SMS Gateway **on**, allow background activity. Settings > Battery > Background app management / Power-saving: SMS Gateway **No restrictions**. In Recents, lock the app. Turn off "Smart power saving" / "Super power saving". |
| Huawei / Honor (EMUI / MagicOS) | Settings > Apps > App launch > SMS Gateway > **Manage manually**: enable Auto-launch, Secondary launch, Run in background. Settings > Battery > App launch and battery optimisation: not optimised. |
| Oppo / Realme (ColorOS / realme UI) | Settings > Apps > App management > SMS Gateway > **Auto-start: on**; Battery usage > **Allow background activity** and **Allow auto launch**. Battery > Optimise battery use > SMS Gateway: **Don't optimise**. Lock the app in Recents. |
| Other / stock Android | Settings > Apps > SMS Gateway > Battery > **Unrestricted**. |

Also on every phone: Settings > Battery > turn off Battery Saver / Power saving mode, and disable
"Adaptive battery" if present.

## 3. Provisioning with the script

The script handles everything that adb can do: installs the APK, grants permissions, raises the
Android SMS limit, whitelists the app from battery optimisation and keeps the screen awake while
charging. It is idempotent (re-run any time) and one failing phone does not stop the others.

### 3.1 Prerequisites
1. Install adb (Android platform-tools). Windows: `C:\Users\<user>\platform_tools` or the Android SDK
   `platform-tools` folder. Linux/macOS: `sudo apt install adb` or `brew install android-platform-tools`.
2. Build the APK (from `android/`): `./gradlew assembleDebug` (`gradlew.bat assembleDebug` on Windows).
   Result: `android/app/build/outputs/apk/debug/app-debug.apk`.
3. Connect phones by USB (a powered hub is fine). On each phone accept the **Allow USB debugging**
   prompt (tick "Always allow"). `adb devices` must show every phone as `device`.

### 3.2 Run it
Windows (PowerShell, from the repo root):

```powershell
# adb on PATH
.\scripts\provision-device.ps1
# adb not on PATH
.\scripts\provision-device.ps1 -Adb C:\Users\<user>\platform_tools\adb.exe
# one phone, custom APK
.\scripts\provision-device.ps1 -Serial R58M123ABC -ApkPath C:\apks\app-debug.apk
```

If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\scripts\provision-device.ps1`.

Linux/macOS:

```sh
scripts/provision-device.sh                       # all connected phones, default APK
scripts/provision-device.sh /path/to/app.apk      # custom APK
scripts/provision-device.sh -s R58M123ABC         # one phone
ADB=~/platform-tools/adb scripts/provision-device.sh
```

### 3.3 Read the summary
The script prints one line per phone:

```
PASS  R58M123ABC  SM-A145F
FAIL  9A2B3C      Redmi 12  missing: WRITE_SECURE_SETTINGS sms-limit(max_count=null)
```

PASS means: package installed, SEND_SMS, READ_PHONE_STATE, WRITE_SECURE_SETTINGS (and
POST_NOTIFICATIONS on Android 13+) granted, SMS limit set, battery whitelist and stay-awake set.
For FAIL see section 8. Fix and re-run, it is safe. Exit code is 0 only if every phone passed.

Order matters on the very first run only because the script installs the app before granting. Never
grant before installing.

## 4. Connect to the server

On each phone (after provisioning and the manual settings above):
1. Open **SMS Gateway**. On the **Connect** screen enter server `https://sms.bita.et` and the
   registration key `<REGISTRATION_KEY>` (ask the admin; never post it in chat or tickets).
2. Tap **Connect**. Accept the SMS, phone and notification prompts if shown (already granted by the
   script, so normally none).
3. Wait for the **Home** screen. The status card turns green: **Connected**.
4. In the admin, confirm the phone appears as a node with its SIMs.

Rate limit on the Home screen defaults to 20 messages/minute; the backend pushes its own value
once connected, so you normally leave it alone.

## 5. Verification checklist (every phone)

1. Provisioning script says **PASS** for the phone.
2. Home status card is **Connected** (green) with a "connected since" time.
3. Home shows **Android SMS limit: Raised**. (Admin dashboard flags phones where it is not.)
4. Both SIMs are listed on Home (dual-SIM phones) with the right carrier names.
5. Send a **test SMS** from the Home screen to your own number on each SIM; confirm it arrives.
6. Battery/autostart settings for the brand are done (table in 2.5).
7. Phone is on its charger, label attached, recorded in the inventory sheet.
8. In the admin, SIM daily caps are set to the pilot values (section 7).

## 6. Android built-in SMS limit

**What it is.** Android's `SmsUsageMonitor` allows an app to send roughly 30 SMS per 30 minutes by
default. This protects users from malware, but it throttles a gateway to about 1 SMS/minute.

**Symptoms.** Either the phone screen shows "This app is sending a large number of messages..."
(with Allow/Deny) and sending stalls until someone taps, or the send fails with
`RESULT_ERROR_LIMIT_EXCEEDED` (code 5), shown in the admin as **LIMIT_EXCEEDED**. The node retries
such messages with backoff, but without the fix the queue keeps growing.

**Fix (done by the provisioning script).**

```sh
adb shell settings put global sms_outgoing_check_max_count 100000
adb shell settings put global sms_outgoing_check_interval_ms 60000
adb shell pm grant com.smsgateway.app android.permission.WRITE_SECURE_SETTINGS
```

The `WRITE_SECURE_SETTINGS` grant lets the app re-apply the two values itself every time the gateway
service starts, so it survives something resetting the setting.

**Verify.**

```sh
adb shell settings get global sms_outgoing_check_max_count    # expect 100000
adb shell settings get global sms_outgoing_check_interval_ms  # expect 60000
```

and Home shows **Android SMS limit: Raised**.

**After a factory reset or reinstall.**
- Factory reset: the global settings and the grant are gone. Re-run the script, then register again.
- Uninstall + install: the `WRITE_SECURE_SETTINGS` grant is lost. Re-run the script.
- `adb install -r` (update in place, which the script uses) keeps the grant.
- Grant only works after the APK is installed.
- Xiaomi (MIUI/HyperOS): enable "USB debugging (Security settings)" or the commands fail with a
  SecurityException.

## 7. Capacity, scheduling and pilot ramp-up

The backend picks a SIM lane per message. Each SIM has a **per-minute rate** (parts/min) and a
**daily cap** (parts/day), both set in the admin. Dual-SIM phones send on both SIMs. A SIM is
**auto-paused** after repeated consecutive failures (likely a carrier block); an admin can resume it.

Server defaults (used when a SIM's own fields are empty in the admin): **10 parts/minute** and
**1000 parts/day** per SIM; a SIM is paused for **60 minutes** after **5** failed messages in a row.
Daily caps reset at midnight Addis Ababa time. These come from `DJANGO_SIM_DEFAULT_RATE_PER_MINUTE`,
`DJANGO_SIM_DEFAULT_DAILY_LIMIT`, `DJANGO_SIM_PAUSE_AFTER_FAILURES`, `DJANGO_SIM_PAUSE_MINUTES` and
`DJANGO_TIME_ZONE` on the server. The admin dashboard (`/admin/`) shows each SIM's usage against its cap.

Caps count message **parts**, not messages:

| Encoding | Single SMS | Per part when split |
|----------|-----------|---------------------|
| GSM-7 (Latin, digits) | 160 chars | 153 |
| Unicode (e.g. Amharic) | 70 chars | 67 |

A 200-character Amharic message is 3 parts. Plan daily capacity in parts.

**Ramp-up for a new SIM.**
1. Day 1-2: set the daily cap to **200/day**. Keep the per-minute rate low (10-20).
2. If delivery rate is healthy (see below) and there are no auto-pauses, raise to **500/day**.
3. Then **1000/day**, then **2000/day**, one step per day or two, never skipping steps.
4. At each step watch: delivery rate (delivered / sent), failure rate, auto-pause events, LIMIT_EXCEEDED.
5. If a SIM gets auto-paused or delivery drops, go back one step and wait 24 hours before resuming.
6. New SIMs and fresh numbers are the most likely to be throttled; ramp them slower. Carriers often
   have their own limits and may block bulk senders, so check the plan or bundle terms.

## 8. Troubleshooting

| Problem | What to do |
|---------|------------|
| Status stuck on **Connecting** or **Disconnected - retrying** | Check Wi-Fi/internet on the phone and that `https://sms.bita.et` opens in its browser. Check the phone's date/time is automatic (wrong clock breaks TLS). Tap **Reconnect now**. Read the last error on the status card. |
| **Unauthorized - re-register needed** | The token was rejected (node deleted in admin, or token reset). Tap **Re-register** (or **Change server** and reconnect with the registration key). |
| Connect says wrong registration key | Get the current key from the admin and retype it; no spaces. |
| **LIMIT_EXCEEDED** errors / "sending a large number of messages" dialog | Android SMS limit is not raised. Re-run the provisioning script; check `settings get global sms_outgoing_check_max_count` is 100000 and Home shows Raised. Tap Allow on any pending dialog. |
| Script FAIL: `WRITE_SECURE_SETTINGS` or `sms-limit` | Xiaomi: enable "USB debugging (Security settings)". Otherwise confirm the APK is installed and the manifest declares the permission; re-run. |
| Script FAIL: `package-not-installed` | Look at the phone screen for an install confirmation, or Play Protect / "Install via USB" blocking. Accept and re-run. |
| Phone not listed by script | `adb devices` shows `unauthorized`: unplug, replug, accept the prompt (or revoke USB debugging authorisations and retry). `offline`: change cable/port, `adb kill-server`. |
| A SIM is **auto-paused** | Likely a carrier block or no balance. Check the SIM (balance, can it send manually?), look at the error codes in the admin, then **resume** the SIM and lower its caps. If it pauses again, rest it 24h. |
| Phone killed overnight / goes Disconnected at night | OEM battery manager. Redo the table in 2.5 for that brand, lock the app in Recents, turn off battery saver, confirm the phone stays on the charger and Wi-Fi does not sleep (Wi-Fi > Advanced > Keep Wi-Fi on during sleep). Check `dumpsys deviceidle whitelist` lists the app. |
| Only one SIM listed | Check the other SIM is active and READ_PHONE_STATE is granted; reopen the app. |
| `SIM_UNAVAILABLE` failures | The job asked for a SIM that is not active (removed, PIN-locked, no signal). Fix the SIM; reregister if SIM layout changed. |
| Messages `sent` but never `delivered` | Delivery reports depend on the carrier and recipient network; some do not return them. If all messages of a SIM lack reports, check the SIM's SMS settings (Messages app > Settings > Delivery reports) and the carrier. `sent` is still a successful hand-off to the network. |
| Queue grows on one phone | Low signal, SIM throttled, or rate set too low. Check signal and raise rate only after the pilot. |

## 9. Re-provisioning and updates

- App update: build a new APK, run the script again (`install -r` keeps data and grants).
- Phone replaced or factory reset: run the script, register again with the key, and re-do 2.5.
- Retire a phone: remove it from the admin, then uninstall the app.
