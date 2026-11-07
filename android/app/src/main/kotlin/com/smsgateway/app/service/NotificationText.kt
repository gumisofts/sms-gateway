package com.smsgateway.app.service

import com.smsgateway.app.gateway.ConnectionState

/** Text of the persistent notification; mirrors the connection status shown in the app. */
object NotificationText {
    fun describe(state: ConnectionState, queueDepth: Int): String = "${connection(state)} - queue: $queueDepth"

    fun connection(state: ConnectionState): String = when (state) {
        ConnectionState.Stopped -> "Off"
        ConnectionState.NotConfigured -> "No server configured (local mode)"
        ConnectionState.Connecting -> "Connecting..."
        ConnectionState.Connected -> "Connected"
        is ConnectionState.Disconnected -> "Reconnecting in ${(state.retryInMs + 999) / 1000}s"
        is ConnectionState.Unauthorized -> "Unauthorized - open the app to re-register"
    }
}
