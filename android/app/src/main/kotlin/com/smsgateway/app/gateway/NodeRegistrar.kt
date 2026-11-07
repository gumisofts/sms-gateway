package com.smsgateway.app.gateway

import com.smsgateway.app.data.SettingsStore

/** Registers this phone with the backend and persists the returned node id + token. */
class NodeRegistrar(
    private val settings: SettingsStore,
    private val api: GatewayApi,
    private val device: DeviceStatusProvider,
) {
    /** Returns the node id. Throws [GatewayApiException] on failure. */
    suspend fun register(): String {
        val s = settings.current()
        if (s.backendUrl.isBlank()) throw GatewayApiException(ApiFailure.InvalidUrl)
        val response = api.register(s.backendUrl, device.registerRequest(s.nodeId, s.registrationKey))
        settings.setCredentials(response.nodeId, response.token)
        return response.nodeId
    }
}
