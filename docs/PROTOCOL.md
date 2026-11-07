# Node <-> Backend protocol (v1)

A node is one Android phone. The backend holds many nodes and dispatches SMS jobs to them.
All payloads are JSON (UTF-8). Implementation: `gateway/Protocol.kt`.

## 1. Registration (REST)

`POST {baseUrl}/api/nodes/register`, `Content-Type: application/json`

Request:

```json
{
  "deviceName": "Pixel 7",
  "manufacturer": "Google",
  "model": "Pixel 7",
  "androidVersion": "15",
  "sdkInt": 35,
  "appVersion": "0.1.0",
  "sims": [{ "slot": 0, "subscriptionId": 3, "carrier": "Vodafone" }],
  "previousNodeId": "node-123",
  "registrationKey": "optional shared secret"
}
```

`previousNodeId` and `registrationKey` are omitted when empty. The backend decides how to authorise
registration (e.g. a pre-shared key) and may reuse the node record for `previousNodeId`.

Response `200`:

```json
{ "nodeId": "node-123", "token": "opaque-bearer-token" }
```

The node persists both (DataStore). Any non-2xx is treated as failure.
Changing the backend URL in the app clears the stored nodeId/token.

## 2. Session (WebSocket)

`GET {baseUrl with http->ws, https->wss}/ws/nodes/{nodeId}` with header
`Authorization: Bearer {token}`.

* HTTP 401/403 on upgrade: token rejected. The node shows "unauthorized" and retries slowly
  (30s base, 10min cap); the user should re-register.
* Any other failure/close: reconnect with exponential backoff (1s base, x2, 60s cap, up to 50%
  subtractive jitter). The delay resets after a connection that opened successfully.
* The node sends WebSocket ping frames every 25s; the server should answer pongs (standard).

Every text frame is a JSON object with a `type` discriminator. Unknown types and unknown fields
are ignored by the node, so the protocol can be extended compatibly.

### Server -> node

`send_sms`: dispatch a message.

```json
{ "type": "send_sms", "id": "job-42", "to": "+15551234567", "body": "Hello", "simSlot": 0, "subscriptionId": null }
```

* `id` is the backend's idempotency key. A re-sent job with a known `id` is acked `accepted: true`
  and not queued twice.
* `simSlot` / `subscriptionId` optional; `subscriptionId` wins. Omit both for the system default SIM.
  If the requested SIM is not active the message fails with `SIM_UNAVAILABLE`.

`config`: runtime tuning (any field optional).

```json
{ "type": "config", "ratePerMinute": 30, "heartbeatIntervalSec": 20 }
```

`ratePerMinute` is persisted on the node (0 = unlimited). `heartbeatIntervalSec` is clamped to 5..600
and lasts for the process lifetime.

### Node -> server

`ack`: sent once per `send_sms`.

```json
{ "type": "ack", "id": "job-42", "accepted": true }
{ "type": "ack", "id": "job-43", "accepted": false, "reason": "queue_full" }
```

Reasons: `invalid_job`, `queue_full`. After `accepted: true` the node owns the job (it is persisted
in a local Room queue and survives restarts). After `accepted: false` the backend should
re-dispatch elsewhere.

`status_update`: every change of a job's state.

```json
{ "type": "status_update", "id": "job-42", "status": "failed", "attempts": 5,
  "errorCode": 4, "errorMessage": "NO_SERVICE", "timestamp": 1760000000000 }
```

`status` is one of `queued` (re-queued for retry), `sending`, `sent` (handed to the network),
`failed`, `delivered` (carrier delivery report). Delivery is **at-least-once**: updates are
persisted and re-sent after a reconnect, and intermediate states may be skipped if several changes
happen while offline (e.g. you may see `sent` straight after `queued`). The backend should treat
`(id, status, attempts)` idempotently and never move a job backwards from a terminal state
(`failed`, `delivered`).

`errorCode` is `SmsManager.RESULT_ERROR_*` (1 generic, 2 radio off, 3 null pdu, 4 no service,
5 limit exceeded, ...) or a node-local negative code: -1 no SEND_SMS permission, -2 exception,
-3 stale (process died mid-send; not retried to avoid duplicates), -4 SIM unavailable.

`heartbeat`: every 30s (default).

```json
{ "type": "heartbeat", "batteryPercent": 81, "charging": false, "signalLevel": 3,
  "sims": [{ "slot": 0, "subscriptionId": 3, "carrier": "Vodafone" }],
  "queueDepth": 2, "ratePerMinute": 20, "timestamp": 1760000000000 }
```

`signalLevel` is 0..4 or omitted. `queueDepth` counts locally queued + sending messages; use it for
load balancing across nodes.

## 3. Retry semantics on the node

* Transient errors (generic failure, radio off, no service, limit exceeded, modem/network errors)
  are re-queued with exponential backoff (30s base, 15min cap), at most 5 attempts, then `failed`.
* Permanent errors fail immediately.
* A message is never retried once any of its multipart pieces was accepted by the radio.
