# SMS Gateway (Android node)

A native Kotlin Android app that turns a phone into one **node** of an SMS gateway. A backend
dispatches outbound SMS jobs to many such phones; each phone sends them through its SIM and
reports per-message status (queued -> sending -> sent / failed -> delivered) back.

This repo currently contains the project setup and the SMS-sending foundation. No backend exists
yet; a "Send test SMS" form enqueues locally so the full sending path can be exercised offline.

## Architecture

```
 UI (Compose, MainViewModel)  ---- only layer that knows about Compose/ViewModel
        |
        v
 GatewayService (foreground, type remoteMessaging)  --- owns the long-running coroutines
   |-- GatewayClient  (gateway/)   OkHttp WebSocket, reconnect w/ backoff, heartbeat,
   |                               send_sms -> queue, status_update <- queue
   |-- SmsDispatcher  (sms/)       queue -> RateLimiter -> SmsSender -> SmsManager
   '-- (settings -> rate limit)
            ^                                  |
            |                       PendingIntents (sent / delivered)
            |                                  v
   MessageRepository (data/) <--------- SmsResultReceiver (aggregates multipart results)
        |
   Room: OutboundMessage queue        DataStore: backend URL, nodeId, token, rate, enabled

 AppContainer (GatewayApp.kt): manual DI, wires everything.
```

* `sms/`, `data/`, `gateway/` contain no UI types (only `android.content.Context` where the
  platform requires it), so they can later be exposed to Flutter through a platform channel
  (e.g. a thin `MethodChannel` over `AppContainer`: start/stop service, enqueue, settings, flows).
* Source of truth is the Room table. Status changes are persisted first; `GatewayClient` pushes
  every change not yet reported (`reportedStatus != status`), so nothing is lost offline.
* Protocol: see [docs/PROTOCOL.md](../docs/PROTOCOL.md).

## Build and install

Requirements: JDK 17+ (built with 21), Android SDK with platform `android-37.0` and build-tools
37.0.0 (`sdk.dir` in `local.properties`, which is git-ignored).

```sh
echo "sdk.dir=$HOME/Android/Sdk" > local.properties
./gradlew assembleDebug testDebugUnitTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

Versions: Gradle 9.8.1, AGP 9.4.1 (built-in Kotlin), Kotlin 2.4.21, KSP 2.3.12, compile/target SDK
37, min SDK 26.

## Using it

**First launch: Connect screen.** Enter the server URL (e.g. `https://gateway.example.com`; a host
without a scheme is assumed to be `https://`, type `http://` explicitly for plain HTTP) and, if the
backend requires one, the registration key. Tap **Connect**. The app asks for the SMS / phone-state
(and, on Android 13+, notification) permissions if needed, then validates the URL, registers the
phone (`POST /api/nodes/register`), starts the foreground service and waits up to 15 s for the
WebSocket. Failures are explained inline (server not found, timeout, connection refused, http blocked
in release builds, TLS error, wrong registration key, not a gateway server (404), server error,
unexpected reply). If the socket is not up after 15 s you still land on Home, which shows the live
status and keeps retrying. **Skip - use offline test mode** goes straight to Home without a server.

**Home screen** (shown whenever the node is registered):

* Status card: Connected (green, with "connected since"), Connecting (amber, pulsing),
  Disconnected - retrying in Ns (red, live countdown; last error shown), Unauthorized - re-register
  needed (red, **Re-register** button), Gateway off (grey, **Turn on** button). It also shows the
  server host/URL and node id. Actions: on/off switch, **Reconnect now** (skips the backoff wait),
  **Change server** (confirm; stops the service, forgets the registration, back to Connect with the URL
  prefilled).
* The persistent notification mirrors the state (Connected / Reconnecting in Ns / Unauthorized / Off).
* Below: setup (permissions, battery exemption), SIMs, test SMS, rate limit (messages/minute, default
  20, also pushed by the backend) and recent messages.

**Local development.** Cleartext `http://`/`ws://` works **only in debug builds** (debug network
security config); release builds require `https://`. A physical phone must reach the backend by its LAN
IP, e.g. `http://192.168.1.10:8000` (not `localhost`; the emulator host is `http://10.0.2.2:8000`), the
backend must listen on that interface (`runserver 0.0.0.0:8000`) and the Django backend needs that IP in
`DJANGO_ALLOWED_HOSTS`, otherwise registration fails with a 400 error.

## Caveats

* **Google Play policy**: `SEND_SMS` is a restricted permission; Play only allows it for apps that
  are the default SMS handler. This app is meant to be side-loaded / distributed privately (MDM,
  direct APK). `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS` is also policy-restricted on Play.
* **Carrier limits**: carriers throttle or block high-volume SIM traffic (and Android itself
  prompts/limits for premium short codes). Keep the rate limit conservative; spread load across nodes.
* **Android 14+ background rules**: foreground services need a declared type. We use
  `remoteMessaging` (documented for relaying text messages; unlike `dataSync` it has no 6h cap on
  Android 15 and may start from BOOT_COMPLETED). The service is `START_STICKY` and restarts on boot
  only if the user had it enabled.
* **OEM battery killers** (Xiaomi, Huawei, Samsung, OnePlus...) may kill the service despite the
  exemption; users must also allow "autostart"/"unrestricted battery" in vendor settings
  (see dontkillmyapp.com).
* The node token is stored in DataStore (app-private, not encrypted). Harden (Keystore) before
  production use.
* Messages stuck in SENDING after a process death are marked failed (`STALE_SENDING`), not resent,
  to avoid duplicate texts.
* Delivery-report parsing handles GSM TP-Status; CDMA networks may report differently.
* Not yet verified on a physical device or emulator (no adb here): sending/receipt flows were
  built and unit-tested only on the JVM.
