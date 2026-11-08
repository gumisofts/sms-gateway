package com.smsgateway.app.gateway

import com.smsgateway.app.sms.SimInfo
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

/** Wire format shared by REST and WebSocket. See docs/PROTOCOL.md at the repo root. */
object GatewayJson {
    val instance: Json = Json {
        classDiscriminator = "type"
        ignoreUnknownKeys = true
        encodeDefaults = false
        explicitNulls = false
    }
}

// ---------------------------------------------------------------- REST

@Serializable
data class RegisterRequest(
    val deviceName: String,
    val manufacturer: String,
    val model: String,
    val androidVersion: String,
    val sdkInt: Int,
    val appVersion: String,
    val sims: List<SimInfo>,
    /** Present when this installation registered before; lets the backend reuse the node record. */
    val previousNodeId: String? = null,
    val registrationKey: String? = null,
)

@Serializable
data class RegisterResponse(
    val nodeId: String,
    val token: String,
)

// ---------------------------------------------------------------- WebSocket: server -> node

@Serializable
sealed class ServerMessage {
    /** Dispatch one SMS. [id] is the backend's idempotency key. */
    @Serializable
    @SerialName("send_sms")
    data class SendSms(
        val id: String,
        val to: String,
        val body: String,
        val simSlot: Int? = null,
        val subscriptionId: Int? = null,
    ) : ServerMessage()

    /** Runtime tuning pushed by the backend. Absent fields are left unchanged. */
    @Serializable
    @SerialName("config")
    data class Config(
        val ratePerMinute: Int? = null,
        val heartbeatIntervalSec: Int? = null,
    ) : ServerMessage()
}

// ---------------------------------------------------------------- WebSocket: node -> server

@Serializable
sealed class NodeMessage {
    /** Reply to every send_sms. accepted=false means the node will NOT send it (see [reason]). */
    @Serializable
    @SerialName("ack")
    data class Ack(
        val id: String,
        val accepted: Boolean,
        val reason: String? = null,
    ) : NodeMessage()

    /** status is one of: queued, sending, sent, failed, delivered. At-least-once; idempotent per (id, status, attempts). */
    @Serializable
    @SerialName("status_update")
    data class StatusUpdate(
        val id: String,
        val status: String,
        val attempts: Int,
        val errorCode: Int? = null,
        val errorMessage: String? = null,
        val timestamp: Long,
        /** SMS parts of the message, when known (omitted before the first send attempt). */
        val parts: Int? = null,
    ) : NodeMessage()

    @Serializable
    @SerialName("heartbeat")
    data class Heartbeat(
        val batteryPercent: Int,
        val charging: Boolean,
        /** 0..4 or null when unknown. */
        val signalLevel: Int? = null,
        val sims: List<SimInfo>,
        val queueDepth: Int,
        val ratePerMinute: Int,
        val timestamp: Long,
        val smsLimit: SmsLimitInfo? = null,
    ) : NodeMessage()
}

/** State of Android's per-app outgoing SMS limit (sms_outgoing_check_*). Null fields = unknown. */
@Serializable
data class SmsLimitInfo(
    val raised: Boolean? = null,
    val maxCount: Int? = null,
    val intervalMs: Long? = null,
)

// ---------------------------------------------------------------- endpoints

object GatewayEndpoints {
    /** Trims and validates a user-entered base URL. Returns null unless it is http(s) with a host. */
    fun normalizeBaseUrl(input: String): String? {
        val s = input.trim().trimEnd('/')
        val ok = (s.startsWith("http://") && s.length > 7) || (s.startsWith("https://") && s.length > 8)
        return if (ok) s else null
    }

    fun register(baseUrl: String): String = baseUrl.trimEnd('/') + "/api/nodes/register"

    fun websocket(baseUrl: String, nodeId: String): String {
        val base = baseUrl.trimEnd('/')
        val ws = when {
            base.startsWith("https://") -> "wss://" + base.removePrefix("https://")
            base.startsWith("http://") -> "ws://" + base.removePrefix("http://")
            else -> base
        }
        val encoded = java.net.URLEncoder.encode(nodeId, "UTF-8").replace("+", "%20")
        return "$ws/ws/nodes/$encoded"
    }
}
