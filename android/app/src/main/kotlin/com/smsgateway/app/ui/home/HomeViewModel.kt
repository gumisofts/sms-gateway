package com.smsgateway.app.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.smsgateway.app.AppContainer
import com.smsgateway.app.data.OutboundMessage
import com.smsgateway.app.data.Settings
import com.smsgateway.app.gateway.GatewayApiException
import com.smsgateway.app.gateway.GatewayStatus
import com.smsgateway.app.service.GatewayService
import com.smsgateway.app.sms.SimInfo
import com.smsgateway.app.sms.SmsLimitStatus
import com.smsgateway.app.ui.connect.toMessage
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class HomeUiState(
    val settings: Settings = Settings(),
    val status: GatewayStatus = GatewayStatus(),
    val sims: List<SimInfo> = emptyList(),
    val messages: List<OutboundMessage> = emptyList(),
    val queueDepth: Int = 0,
    val smsLimit: SmsLimitStatus = SmsLimitStatus(null, null, null),
    val reRegistering: Boolean = false,
    val info: String? = null,
)

class HomeViewModel(private val container: AppContainer) : ViewModel() {
    private val sims = MutableStateFlow<List<SimInfo>>(emptyList())
    private val smsLimit = MutableStateFlow(SmsLimitStatus(null, null, null))
    private val transient = MutableStateFlow(false to (null as String?))

    val state: StateFlow<HomeUiState> = combine(
        container.settings.flow,
        GatewayService.status,
        sims,
        combine(container.messages.observeRecent(50), container.messages.observeQueueDepth(), transient, smsLimit) { m, q, t, l -> listOf(m, q, t, l) },
    ) { settings, status, simList, parts ->
        @Suppress("UNCHECKED_CAST")
        val t = parts[2] as Pair<Boolean, String?>
        @Suppress("UNCHECKED_CAST")
        HomeUiState(settings, status, simList, parts[0] as List<OutboundMessage>, parts[1] as Int, parts[3] as SmsLimitStatus, t.first, t.second)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), HomeUiState())

    private val context get() = container.appContext

    fun refreshSims() {
        sims.value = container.simManager.activeSims()
    }

    fun refreshSmsLimit() {
        smsLimit.value = container.smsLimit.status()
    }

    /** Tries to raise Android's SMS limit (needs the adb-granted permission), then re-reads it. */
    fun applySmsLimit() {
        container.smsLimit.apply()
        refreshSmsLimit()
    }

    fun setServiceEnabled(enabled: Boolean) {
        viewModelScope.launch {
            container.settings.setServiceEnabled(enabled)
            if (enabled) GatewayService.start(context) else GatewayService.stop(context)
        }
    }

    fun reconnectNow() = GatewayService.reconnectNow(context)

    /** Registers again with the saved URL / key; the service reconnects when the new token is stored. */
    fun reRegister() {
        viewModelScope.launch {
            transient.value = true to null
            val msg = try {
                container.registrar.register()
                "Registered again"
            } catch (e: GatewayApiException) {
                e.failure.toMessage().let { "${it.title}. ${it.hint}" }
            }
            transient.value = false to msg
        }
    }

    /** Stops the service, forgets this node's credentials (the URL stays for prefilling) and returns to Connect. */
    fun changeServer() {
        viewModelScope.launch {
            container.settings.setServiceEnabled(false)
            GatewayService.stop(context)
            container.settings.clearCredentials()
            container.settings.setLocalMode(false)
            transient.value = false to null
        }
    }

    fun setRate(perMinute: Int) {
        viewModelScope.launch {
            container.settings.setRatePerMinute(perMinute)
            transient.update { it.first to "Rate limit set" }
        }
    }

    fun sendTest(to: String, body: String, subscriptionId: Int?) {
        viewModelScope.launch {
            if (to.isBlank() || body.isBlank()) {
                transient.update { it.first to "Enter a number and a message" }
                return@launch
            }
            val slot = sims.value.firstOrNull { it.subscriptionId == subscriptionId }?.slot
            container.messages.enqueueLocal(to.trim(), body, subscriptionId, slot)
            val running = GatewayService.running.value
            transient.update { it.first to if (running) "Queued test message" else "Queued - turn the gateway on to send" }
        }
    }
}
