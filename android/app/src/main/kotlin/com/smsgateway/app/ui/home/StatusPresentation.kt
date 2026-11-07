package com.smsgateway.app.ui.home

import com.smsgateway.app.gateway.ConnectionState
import com.smsgateway.app.gateway.GatewayStatus

enum class StatusTone { Connected, Connecting, Error, Off }
enum class StatusAction { ReRegister, TurnOn }

data class StatusPresentation(
    val tone: StatusTone,
    val label: String,
    val action: StatusAction? = null,
    /** The indicator should pulse. */
    val animated: Boolean = false,
    /** The label depends on the clock, so the UI must re-evaluate every second. */
    val ticking: Boolean = false,
    /** "Reconnect now" makes sense (we are waiting out a backoff). */
    val canReconnectNow: Boolean = false,
)

fun countdownSeconds(retryAt: Long, now: Long): Long = ((retryAt - now).coerceAtLeast(0) + 999) / 1000

fun formatCountdown(seconds: Long): String =
    if (seconds < 60) "${seconds}s" else "${seconds / 60}m ${(seconds % 60).toString().padStart(2, '0')}s"

/** Pure mapping from published status to what the status card shows. [now] is wall-clock millis. */
fun presentStatus(status: GatewayStatus, hasCredentials: Boolean, now: Long): StatusPresentation {
    if (!hasCredentials) return StatusPresentation(StatusTone.Off, "No server configured")
    return when (val c = status.connection) {
        ConnectionState.Stopped -> StatusPresentation(StatusTone.Off, "Gateway off", StatusAction.TurnOn)
        ConnectionState.NotConfigured, ConnectionState.Connecting ->
            StatusPresentation(StatusTone.Connecting, "Connecting...", animated = true)
        ConnectionState.Connected -> StatusPresentation(StatusTone.Connected, "Connected")
        is ConnectionState.Disconnected -> {
            val secs = status.retryAt?.let { countdownSeconds(it, now) } ?: (c.retryInMs / 1000)
            val label = if (secs > 0) "Disconnected – retrying in ${formatCountdown(secs)}" else "Disconnected – retrying..."
            StatusPresentation(StatusTone.Error, label, ticking = true, canReconnectNow = true)
        }
        is ConnectionState.Unauthorized -> StatusPresentation(
            StatusTone.Error, "Unauthorized – re-register needed", StatusAction.ReRegister, canReconnectNow = true,
        )
    }
}
