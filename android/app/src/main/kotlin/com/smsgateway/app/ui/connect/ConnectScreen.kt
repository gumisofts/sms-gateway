package com.smsgateway.app.ui.connect

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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smsgateway.app.gateway.ServerUrl
import com.smsgateway.app.gateway.UrlParseResult
import com.smsgateway.app.ui.criticalPermissions
import com.smsgateway.app.ui.missingPermissions
import com.smsgateway.app.ui.permissionLabel

@Composable
fun ConnectScreen(viewModel: ConnectViewModel) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var showKey by rememberSaveable { mutableStateOf(false) }

    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        val deniedCritical = context.missingPermissions().filter { it in criticalPermissions }
        if (deniedCritical.isEmpty()) viewModel.connect() else viewModel.onPermissionsDenied(deniedCritical.map(::permissionLabel))
    }
    val connect = {
        if (viewModel.validate()) {
            val missing = context.missingPermissions()
            if (missing.isEmpty()) viewModel.connect() else permissionLauncher.launch(missing.toTypedArray())
        }
    }

    val schemeHint = (ServerUrl.parse(state.url) as? UrlParseResult.Valid)?.takeIf { it.schemeAssumed }?.url

    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal = 24.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("Connect to your server", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(top = 24.dp))
        Text(
            "Enter the address of your SMS gateway backend. This phone will register itself and start relaying messages.",
            style = MaterialTheme.typography.bodyMedium,
        )

        OutlinedTextField(
            value = state.url,
            onValueChange = viewModel::onUrlChange,
            label = { Text("Server URL") },
            placeholder = { Text("https://gateway.example.com") },
            singleLine = true,
            enabled = !state.busy,
            isError = state.urlError != null,
            supportingText = {
                Text(state.urlError ?: schemeHint?.let { "Will connect to $it (type http:// to use plain HTTP)" } ?: "")
            },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.key,
            onValueChange = viewModel::onKeyChange,
            label = { Text("Registration key (optional)") },
            singleLine = true,
            enabled = !state.busy,
            visualTransformation = if (showKey) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            trailingIcon = { TextButton(onClick = { showKey = !showKey }) { Text(if (showKey) "Hide" else "Show") } },
            modifier = Modifier.fillMaxWidth(),
        )

        if (state.busy) ProgressSteps(state.step)

        state.error?.let { err ->
            Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.errorContainer), modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(err.title, style = MaterialTheme.typography.titleSmall, color = MaterialTheme.colorScheme.onErrorContainer)
                    Text(err.hint, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onErrorContainer)
                }
            }
        }
        state.permissionNote?.let { note ->
            Text(note, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            TextButton(onClick = {
                context.startActivity(
                    Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, "package:${context.packageName}".toUri())
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }) { Text("Open app settings") }
        }

        Button(onClick = connect, enabled = !state.busy && state.url.isNotBlank(), modifier = Modifier.fillMaxWidth()) {
            Text(if (state.error != null) "Try again" else "Connect")
        }
        TextButton(onClick = viewModel::useOfflineMode, enabled = !state.busy, modifier = Modifier.align(Alignment.CenterHorizontally)) {
            Text("Skip – use offline test mode")
        }
    }
}

@Composable
private fun ProgressSteps(current: ConnectStep) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        ConnectStep.entries.filter { it != ConnectStep.Idle }.forEach { step ->
            val done = step.ordinal < current.ordinal
            val active = step == current
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                when {
                    active -> CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    done -> Text("✓", color = MaterialTheme.colorScheme.primary)
                    else -> Text("•", color = MaterialTheme.colorScheme.outline)
                }
                Text(
                    step.label,
                    color = if (active || done) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.outline,
                )
            }
        }
    }
}
