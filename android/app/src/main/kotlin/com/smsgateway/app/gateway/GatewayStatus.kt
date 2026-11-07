package com.smsgateway.app.gateway

/**
 * Connection state plus the timestamps the UI needs. Everything is wall-clock millis so the UI can
 * derive countdowns / durations itself instead of trusting a value captured at emit time.
 */
data class GatewayStatus(
    val connection: ConnectionState = ConnectionState.Stopped,
    /** When [connection] last changed. */
    val stateSince: Long = 0L,
    /** Non-null only while Connected. */
    val connectedSince: Long? = null,
    /** Last time a connection was up (kept after it drops). */
    val lastConnectedAt: Long? = null,
    val lastError: String? = null,
) {
    /** When the next reconnect attempt starts, for the states that wait. */
    val retryAt: Long?
        get() = when (val c = connection) {
            is ConnectionState.Disconnected -> stateSince + c.retryInMs
            is ConnectionState.Unauthorized -> stateSince + c.retryInMs
            else -> null
        }

    fun next(new: ConnectionState, now: Long): GatewayStatus {
        val wasConnected = connection == ConnectionState.Connected
        val lastUp = if (wasConnected && new != ConnectionState.Connected) now else lastConnectedAt
        return GatewayStatus(
            connection = new,
            stateSince = now,
            connectedSince = when {
                new != ConnectionState.Connected -> null
                wasConnected -> connectedSince
                else -> now
            },
            lastConnectedAt = if (new == ConnectionState.Connected) now else lastUp,
            lastError = when (new) {
                is ConnectionState.Disconnected -> new.reason ?: lastError
                is ConnectionState.Unauthorized -> "Server rejected this node's credentials"
                ConnectionState.Connected -> null
                else -> lastError
            },
        )
    }
}
