package com.smsgateway.app.gateway

import android.util.Log
import com.smsgateway.app.data.EnqueueResult
import com.smsgateway.app.data.MessageRepository
import com.smsgateway.app.data.NodeCredentials
import com.smsgateway.app.data.SettingsStore
import com.smsgateway.app.util.Backoff
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.launch
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

sealed interface ConnectionState {
    /** The foreground service is not running. */
    data object Stopped : ConnectionState
    data object NotConfigured : ConnectionState
    data object Connecting : ConnectionState
    data object Connected : ConnectionState
    data class Disconnected(val reason: String?, val retryInMs: Long) : ConnectionState
    data class Unauthorized(val retryInMs: Long) : ConnectionState
}

/**
 * Keeps one WebSocket to the backend alive (exponential backoff + jitter), turns `send_sms` jobs into
 * queue rows, and streams status updates and heartbeats. Pure Kotlin + OkHttp, no UI types.
 */
class GatewayClient(
    private val settings: SettingsStore,
    private val http: OkHttpClient,
    private val messages: MessageRepository,
    private val device: DeviceStatusProvider,
    private val json: Json = GatewayJson.instance,
) {
    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.NotConfigured)
    val state: StateFlow<ConnectionState> = _state

    @Volatile private var heartbeatIntervalMs = DEFAULT_HEARTBEAT_MS

    private val wake = Channel<Unit>(Channel.CONFLATED)
    @Volatile private var waitingToRetry = false

    /** Skips the remaining backoff wait (no-op unless currently waiting to reconnect). */
    fun reconnectNow() {
        if (waitingToRetry) wake.trySend(Unit)
    }

    /** Runs until cancelled; restarts the connection when backend URL / credentials change. */
    suspend fun run() {
        settings.flow.map { it.credentials() }.distinctUntilChanged().collectLatest { creds ->
            if (creds == null) {
                _state.value = ConnectionState.NotConfigured
                return@collectLatest
            }
            var attempt = 0
            while (true) {
                _state.value = ConnectionState.Connecting
                val result = try {
                    connectOnce(creds)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    Attempt(opened = false, unauthorized = false, error = e.message)
                }
                attempt = if (result.opened) 1 else attempt + 1
                val wait = if (result.unauthorized) {
                    Backoff.delayMs(attempt, 30_000, 10 * 60_000, jitter = 0.5)
                } else {
                    Backoff.delayMs(attempt, 1_000, 60_000, jitter = 0.5)
                }
                _state.value = if (result.unauthorized) ConnectionState.Unauthorized(wait) else ConnectionState.Disconnected(result.error, wait)
                wake.tryReceive() // drop a stale wake-up
                waitingToRetry = true
                try {
                    withTimeoutOrNull(wait) { wake.receive() }
                } finally {
                    waitingToRetry = false
                }
            }
        }
    }

    private class Attempt(val opened: Boolean, val unauthorized: Boolean, val error: String?)

    private suspend fun connectOnce(creds: NodeCredentials): Attempt = coroutineScope {
        val opened = CompletableDeferred<Unit>()
        val closed = CompletableDeferred<Unit>()
        val incoming = Channel<String>(Channel.UNLIMITED)
        val wasOpen = AtomicBoolean(false)
        val failure = AtomicReference<String?>(null)
        val httpCode = AtomicReference<Int?>(null)

        val request = Request.Builder()
            .url(GatewayEndpoints.websocket(creds.baseUrl, creds.nodeId))
            .header("Authorization", "Bearer ${creds.token}")
            .build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                wasOpen.set(true)
                opened.complete(Unit)
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                incoming.trySend(text)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                failure.set("closed by server ($code) $reason".trim())
                webSocket.close(1000, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                closed.complete(Unit)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                failure.set(t.message ?: t.javaClass.simpleName)
                httpCode.set(response?.code)
                closed.complete(Unit)
            }
        }

        val ws = http.newWebSocket(request, listener)
        val sender: (NodeMessage) -> Boolean = { ws.send(json.encodeToString(NodeMessage.serializer(), it)) }

        val workers = listOf(
            launch { opened.await(); _state.value = ConnectionState.Connected; for (t in incoming) handleIncoming(t, sender) },
            launch { opened.await(); heartbeatLoop(sender) },
            launch { opened.await(); reportStatusLoop(sender) },
        )
        try {
            closed.await()
        } finally {
            workers.forEach { it.cancel() }
            incoming.close()
            ws.close(1000, "bye")
        }
        val code = httpCode.get()
        Attempt(wasOpen.get(), code == 401 || code == 403, failure.get())
    }

    private suspend fun handleIncoming(text: String, send: (NodeMessage) -> Boolean) {
        val msg = try {
            json.decodeFromString(ServerMessage.serializer(), text)
        } catch (e: SerializationException) {
            Log.w(TAG, "Ignoring unparseable server message: ${e.message}")
            return
        }
        when (msg) {
            is ServerMessage.SendSms -> {
                val reply = if (msg.id.isBlank() || msg.to.isBlank() || msg.body.isEmpty()) {
                    NodeMessage.Ack(msg.id, false, "invalid_job")
                } else {
                    when (messages.enqueueRemote(msg.id, msg.to, msg.body, msg.simSlot, msg.subscriptionId)) {
                        EnqueueResult.ACCEPTED, EnqueueResult.DUPLICATE -> NodeMessage.Ack(msg.id, true)
                        EnqueueResult.QUEUE_FULL -> NodeMessage.Ack(msg.id, false, "queue_full")
                    }
                }
                send(reply)
            }
            is ServerMessage.Config -> {
                msg.ratePerMinute?.let { settings.setRatePerMinute(it) }
                msg.heartbeatIntervalSec?.let { heartbeatIntervalMs = it.coerceIn(5, 600) * 1000L }
            }
        }
    }

    private suspend fun heartbeatLoop(send: (NodeMessage) -> Boolean) {
        while (true) {
            val rate = settings.current().ratePerMinute
            send(device.heartbeat(messages.queueDepth(), rate))
            delay(heartbeatIntervalMs)
        }
    }

    /** Pushes every status change not yet handed to the socket (also covers changes made while offline). */
    private suspend fun reportStatusLoop(send: (NodeMessage) -> Boolean) {
        messages.observeUnreported().collectLatest { pending ->
            for (m in pending) {
                val update = NodeMessage.StatusUpdate(m.id, m.status.wire, m.attempts, m.errorCode, m.errorMessage, m.updatedAt)
                if (!send(update)) return@collectLatest // socket is closing; retried after reconnect
                messages.markReported(m.id, m.status)
            }
        }
    }

    private companion object {
        const val TAG = "GatewayClient"
        const val DEFAULT_HEARTBEAT_MS = 30_000L
    }
}
