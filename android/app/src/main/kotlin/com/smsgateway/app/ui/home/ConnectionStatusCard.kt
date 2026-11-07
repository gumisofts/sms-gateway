package com.smsgateway.app.ui.home

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.smsgateway.app.data.Settings
import com.smsgateway.app.gateway.ConnectionState
import com.smsgateway.app.gateway.GatewayStatus
import com.smsgateway.app.gateway.ServerUrl
import kotlinx.coroutines.delay
import java.text.DateFormat
import java.util.Date

private val Green = Color(0xFF2E7D32)
private val Amber = Color(0xFFF9A825)
private val Grey = Color(0xFF9E9E9E)

@Composable
private fun StatusTone.color(): Color = when (this) {
    StatusTone.Connected -> Green
    StatusTone.Connecting -> Amber
    StatusTone.Error -> MaterialTheme.colorScheme.error
    StatusTone.Off -> Grey
}

@Composable
fun ConnectionStatusCard(
    settings: Settings,
    status: GatewayStatus,
    reRegistering: Boolean,
    onToggle: (Boolean) -> Unit,
    onReconnectNow: () -> Unit,
    onReRegister: () -> Unit,
    onTurnOn: () -> Unit,
    onChangeServer: () -> Unit,
) {
    val hasCreds = settings.credentials() != null
    // Wall clock that ticks once a second only while the label depends on it.
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val ticking = hasCreds && status.connection is ConnectionState.Disconnected
    LaunchedEffect(ticking, status.stateSince) {
        now = System.currentTimeMillis()
        while (ticking) {
            delay(1000)
            now = System.currentTimeMillis()
        }
    }
    val p = presentStatus(status, hasCreds, now)
    val color = p.tone.color()
    val pulse by rememberInfiniteTransition(label = "pulse").animateFloat(
        initialValue = 1f, targetValue = 0.25f,
        animationSpec = infiniteRepeatable(tween(800), RepeatMode.Reverse), label = "pulseAlpha",
    )

    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Box(
                    Modifier.size(28.dp).alpha(if (p.animated) pulse else 1f).background(color, CircleShape),
                )
                Text(p.label, style = MaterialTheme.typography.titleLarge, color = color, modifier = Modifier.weight(1f))
                Switch(checked = settings.serviceEnabled, onCheckedChange = onToggle)
            }

            if (hasCreds) {
                Text(ServerUrl.displayHost(settings.backendUrl), style = MaterialTheme.typography.headlineSmall)
                Text(settings.backendUrl, style = MaterialTheme.typography.bodySmall)
                Text("Node id: ${settings.nodeId}", style = MaterialTheme.typography.bodySmall)
                val fmt = DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.MEDIUM)
                status.connectedSince?.let { Text("Connected since ${fmt.format(Date(it))}") }
                    ?: Text(status.lastConnectedAt?.let { "Last connected ${fmt.format(Date(it))}" } ?: "Not connected yet")
                if (p.tone == StatusTone.Error) status.lastError?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                }
            } else {
                Text("Offline test mode: messages are queued and sent from this phone only.", style = MaterialTheme.typography.bodyMedium)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
                when (p.action) {
                    StatusAction.ReRegister -> Button(onClick = onReRegister, enabled = !reRegistering) {
                        Text(if (reRegistering) "Registering..." else "Re-register")
                    }
                    StatusAction.TurnOn -> Button(onClick = onTurnOn) { Text("Turn on") }
                    null -> {}
                }
                if (hasCreds) OutlinedButton(onClick = onReconnectNow, enabled = p.canReconnectNow) { Text("Reconnect now") }
                TextButton(onClick = onChangeServer) { Text(if (hasCreds) "Change server" else "Set up server") }
            }
        }
    }
}
