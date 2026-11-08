package com.smsgateway.app.ui.home

import android.content.Intent
import androidx.core.net.toUri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smsgateway.app.data.MessageStatus
import com.smsgateway.app.data.OutboundMessage
import com.smsgateway.app.ui.ignoringBatteryOptimizations
import com.smsgateway.app.ui.missingPermissions
import com.smsgateway.app.ui.permissionLabel
import java.text.DateFormat
import java.util.Date

@Composable
fun HomeScreen(viewModel: HomeViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    // Bumped whenever permissions / battery settings may have changed, to re-read them.
    var refresh by remember { mutableIntStateOf(0) }
    val missing = remember(refresh) { context.missingPermissions() }
    val batteryOk = remember(refresh) { context.ignoringBatteryOptimizations() }

    val lifecycleOwner = LocalLifecycleOwner.current
    DisposableEffect(lifecycleOwner) {
        val obs = LifecycleEventObserver { _, e -> if (e == Lifecycle.Event.ON_RESUME) refresh++ }
        lifecycleOwner.lifecycle.addObserver(obs)
        onDispose { lifecycleOwner.lifecycle.removeObserver(obs) }
    }
    LaunchedEffect(refresh) { viewModel.refreshSims(); viewModel.refreshSmsLimit() }

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { refresh++ }
    val batteryLauncher = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { refresh++ }
    var confirmChange by rememberSaveable { mutableStateOf(false) }

    if (confirmChange) {
        val hasCreds = state.settings.credentials() != null
        AlertDialog(
            onDismissRequest = { confirmChange = false },
            title = { Text(if (hasCreds) "Change server?" else "Set up a server?") },
            text = {
                Text(
                    if (hasCreds) {
                        "This disconnects the gateway, turns it off and forgets this phone's registration. " +
                            "You will be taken back to the Connect screen with the current URL filled in."
                    } else {
                        "Go to the Connect screen to add a server URL."
                    },
                )
            },
            confirmButton = { TextButton(onClick = { confirmChange = false; viewModel.changeServer() }) { Text("Continue") } },
            dismissButton = { TextButton(onClick = { confirmChange = false }) { Text("Cancel") } },
        )
    }

    LazyColumn(
        Modifier.fillMaxWidth().padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        item { Text("SMS Gateway node", style = MaterialTheme.typography.headlineSmall, modifier = Modifier.padding(top = 8.dp)) }

        item {
            ConnectionStatusCard(
                settings = state.settings,
                status = state.status,
                reRegistering = state.reRegistering,
                onToggle = viewModel::setServiceEnabled,
                onReconnectNow = viewModel::reconnectNow,
                onReRegister = viewModel::reRegister,
                onTurnOn = { viewModel.setServiceEnabled(true) },
                onChangeServer = { confirmChange = true },
            )
        }
        state.info?.let { info -> item { Text(info, style = MaterialTheme.typography.bodyMedium) } }

        if (missing.isNotEmpty() || !batteryOk) item {
            Card(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Setup required", style = MaterialTheme.typography.titleMedium)
                    if (missing.isNotEmpty()) {
                        Text("Missing permissions: " + missing.joinToString { permissionLabel(it) })
                        Button(onClick = { permissionLauncher.launch(missing.toTypedArray()) }) { Text("Grant permissions") }
                    }
                    if (!batteryOk) {
                        Text("Battery optimization may kill the service. Exempt this app.")
                        OutlinedButton(onClick = {
                            batteryLauncher.launch(
                                Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, "package:${context.packageName}".toUri()),
                            )
                        }) { Text("Ignore battery optimizations") }
                    }
                }
            }
        }

        item { SmsLimitCard(state.smsLimit.raised == true, viewModel::applySmsLimit) }
        item { SimsCard(state) }
        item { TestSendCard(state, viewModel) }
        item { SettingsCard(state.settings.ratePerMinute, viewModel::setRate) }

        item { Text("Recent messages (${state.queueDepth} active)", style = MaterialTheme.typography.titleMedium) }
        items(state.messages, key = { it.id }) { MessageRow(it) }
        item { Text("", Modifier.padding(16.dp)) }
    }
}

@Composable
private fun SmsLimitCard(raised: Boolean, onApply: () -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Android SMS limit", style = MaterialTheme.typography.titleMedium)
            if (raised) {
                Text("Raised", color = Color(0xFF2E7D32))
            } else {
                Text(
                    "Android limits apps to ~30 SMS / 30 min. Run the provisioning script (docs/DEVICE_SETUP.md) or: " +
                        "adb shell pm grant com.smsgateway.app android.permission.WRITE_SECURE_SETTINGS",
                    color = MaterialTheme.colorScheme.error,
                )
                OutlinedButton(onClick = onApply) { Text("Re-check / Apply") }
            }
        }
    }
}

@Composable
private fun SettingsCard(ratePerMinute: Int, onApply: (Int) -> Unit) {
    var rate by remember(ratePerMinute) { mutableStateOf(ratePerMinute.toString()) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Settings", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(
                rate, { rate = it.filter(Char::isDigit).take(3) },
                label = { Text("Rate limit (messages/minute, 0 = unlimited)") }, singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedButton(onClick = { onApply(rate.toIntOrNull() ?: 0) }) { Text("Apply rate") }
        }
    }
}

@Composable
private fun SimsCard(state: HomeUiState) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text("SIMs", style = MaterialTheme.typography.titleMedium)
            if (state.sims.isEmpty()) Text("None detected (grant phone state permission / insert a SIM)")
            state.sims.forEach { Text("Slot ${it.slot}: ${it.carrier} (subscription ${it.subscriptionId})") }
        }
    }
}

@Composable
private fun TestSendCard(state: HomeUiState, vm: HomeViewModel) {
    var to by rememberSaveable { mutableStateOf("") }
    var body by rememberSaveable { mutableStateOf("") }
    var subId by rememberSaveable { mutableStateOf<Int?>(null) }
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("Send test SMS (local queue)", style = MaterialTheme.typography.titleMedium)
            OutlinedTextField(to, { to = it }, label = { Text("To") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(body, { body = it }, label = { Text("Message") }, modifier = Modifier.fillMaxWidth())
            if (state.sims.size > 1) {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = subId == null, onClick = { subId = null }, label = { Text("Default SIM") })
                    state.sims.forEach {
                        FilterChip(selected = subId == it.subscriptionId, onClick = { subId = it.subscriptionId }, label = { Text("SIM ${it.slot + 1}") })
                    }
                }
            }
            Button(onClick = { vm.sendTest(to, body, subId) }) { Text("Queue message") }
        }
    }
}

@Composable
private fun MessageRow(m: OutboundMessage) {
    Column {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            Text(m.to, style = MaterialTheme.typography.bodyLarge)
            Text(m.status.name, color = statusColor(m.status), style = MaterialTheme.typography.labelLarge)
        }
        Text(m.body.take(80), style = MaterialTheme.typography.bodySmall, maxLines = 2)
        val detail = buildString {
            append(DateFormat.getTimeInstance(DateFormat.MEDIUM).format(Date(m.updatedAt)))
            append(" - attempts ${m.attempts}")
            m.errorMessage?.let { append(" - $it") }
        }
        Text(detail, style = MaterialTheme.typography.labelSmall)
        HorizontalDivider(Modifier.padding(top = 8.dp))
    }
}

@Composable
private fun statusColor(s: MessageStatus) = when (s) {
    MessageStatus.FAILED -> MaterialTheme.colorScheme.error
    MessageStatus.DELIVERED, MessageStatus.SENT -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.onSurface
}
