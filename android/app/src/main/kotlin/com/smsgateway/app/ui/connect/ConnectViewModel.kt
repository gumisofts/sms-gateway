package com.smsgateway.app.ui.connect

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.smsgateway.app.AppContainer
import com.smsgateway.app.data.Settings
import com.smsgateway.app.gateway.ApiFailure
import com.smsgateway.app.gateway.ConnectionState
import com.smsgateway.app.gateway.GatewayApiException
import com.smsgateway.app.gateway.ServerUrl
import com.smsgateway.app.gateway.UrlParseResult
import com.smsgateway.app.gateway.UrlProblem
import com.smsgateway.app.service.GatewayService
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

enum class Route { Connect, Home }

/** Home when the node is registered (or the user chose offline mode); the Connect screen stays up while connecting. */
fun chooseRoute(settings: Settings, connecting: Boolean): Route =
    if (!connecting && (settings.credentials() != null || settings.localMode)) Route.Home else Route.Connect

enum class ConnectStep(val label: String) {
    Idle(""),
    Contacting("Contacting server..."),
    Registering("Registering device..."),
    Opening("Opening connection..."),
}

data class ConnectUiState(
    val url: String = "",
    val key: String = "",
    val urlError: String? = null,
    val step: ConnectStep = ConnectStep.Idle,
    val error: ErrorMessage? = null,
    val permissionNote: String? = null,
) {
    val busy get() = step != ConnectStep.Idle
}

/**
 * Owns the Connect flow (validate, save, register, start service, wait for the socket) in
 * [viewModelScope], so it survives rotation. Also decides which top-level screen is shown.
 */
class ConnectViewModel(private val container: AppContainer) : ViewModel() {
    private val _state = MutableStateFlow(ConnectUiState())
    val state: StateFlow<ConnectUiState> = _state

    /** Null until settings have been read from disk (avoids flashing the Connect screen on start). */
    val route: StateFlow<Route?> = combine(container.settings.flow, _state.map { it.busy }.distinctUntilChanged()) { s, busy ->
        chooseRoute(s, busy)
    }.stateIn(viewModelScope, SharingStarted.Eagerly, null)

    private var job: Job? = null

    init {
        // Prefill from saved settings (initially, and again after "Change server"); typing never touches settings.
        viewModelScope.launch {
            container.settings.flow.map { it.backendUrl to it.registrationKey }.distinctUntilChanged().collect { (url, key) ->
                if (!_state.value.busy) _state.update { it.copy(url = url, key = key) }
            }
        }
    }

    fun onUrlChange(value: String) = _state.update { it.copy(url = value, urlError = null, error = null, permissionNote = null) }
    fun onKeyChange(value: String) = _state.update { it.copy(key = value, error = null) }

    /** Validates the URL and shows an inline error. Returns whether it is usable. */
    fun validate(): Boolean {
        val result = ServerUrl.parse(_state.value.url)
        if (result is UrlParseResult.Invalid) {
            _state.update { it.copy(urlError = urlProblemText(result.problem)) }
            return false
        }
        return true
    }

    fun onPermissionsDenied(denied: List<String>) {
        _state.update {
            it.copy(
                permissionNote = "${denied.joinToString()} are needed to send text messages and read your SIMs. " +
                    "Allow them when asked, or enable them in the app's system settings, then tap Connect again.",
            )
        }
    }

    fun connect() {
        if (job?.isActive == true) return
        val snapshot = _state.value
        val parsed = ServerUrl.parse(snapshot.url)
        if (parsed !is UrlParseResult.Valid) {
            validate()
            return
        }
        job = viewModelScope.launch {
            _state.update { it.copy(urlError = null, error = null, permissionNote = null, step = ConnectStep.Contacting) }
            try {
                container.settings.setBackend(parsed.url, snapshot.key)
                _state.update { it.copy(url = parsed.url, step = ConnectStep.Registering) }
                container.registrar.register()
                container.settings.setServiceEnabled(true)
                _state.update { it.copy(step = ConnectStep.Opening) }
                GatewayService.start(container.appContext)
                // Either way we move on to Home, which shows the live status and keeps retrying.
                withTimeoutOrNull(CONNECT_WAIT_MS) {
                    GatewayService.status.first {
                        it.connection == ConnectionState.Connected || it.connection is ConnectionState.Unauthorized
                    }
                }
                _state.update { it.copy(step = ConnectStep.Idle) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: GatewayApiException) {
                _state.update { it.copy(step = ConnectStep.Idle, error = e.failure.toMessage()) }
            } catch (e: Exception) {
                _state.update {
                    it.copy(step = ConnectStep.Idle, error = ErrorMessage("Something went wrong", e.message ?: e.javaClass.simpleName))
                }
            }
        }
    }

    fun useOfflineMode() {
        viewModelScope.launch { container.settings.setLocalMode(true) }
    }

    private fun urlProblemText(p: UrlProblem) = when (p) {
        UrlProblem.Empty -> "Enter the server URL"
        UrlProblem.UnsupportedScheme -> "Only http:// and https:// are supported"
        UrlProblem.InvalidHost -> ApiFailure.InvalidUrl.toMessage().title
    }

    companion object {
        const val CONNECT_WAIT_MS = 15_000L
    }
}
