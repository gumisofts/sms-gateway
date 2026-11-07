package com.smsgateway.app.data

import android.content.Context
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "gateway_settings")

data class NodeCredentials(val baseUrl: String, val nodeId: String, val token: String)

data class Settings(
    val backendUrl: String = "",
    val registrationKey: String = "",
    val nodeId: String? = null,
    val token: String? = null,
    val ratePerMinute: Int = DEFAULT_RATE_PER_MINUTE,
    val serviceEnabled: Boolean = false,
    /** User chose "offline test mode" instead of connecting to a backend. */
    val localMode: Boolean = false,
) {
    /** Non-null only when a backend URL is set and the node has registered. */
    fun credentials(): NodeCredentials? =
        if (backendUrl.isNotBlank() && !nodeId.isNullOrBlank() && !token.isNullOrBlank()) {
            NodeCredentials(backendUrl, nodeId, token)
        } else {
            null
        }

    companion object {
        const val DEFAULT_RATE_PER_MINUTE = 20
    }
}

/** DataStore-backed settings. The node token is stored here too (app-private storage, not encrypted). */
class SettingsStore(private val context: Context) {
    val flow: Flow<Settings> = context.dataStore.data.map { it.toSettings() }

    suspend fun current(): Settings = context.dataStore.data.map { it.toSettings() }.first()

    suspend fun setBackend(url: String, registrationKey: String) = context.dataStore.edit {
        val normalized = url.trim().trimEnd('/')
        // Pointing at a different backend invalidates the old registration.
        if (normalized != it[BACKEND_URL].orEmpty()) {
            it.remove(NODE_ID)
            it.remove(TOKEN)
        }
        it[BACKEND_URL] = normalized
        it[REG_KEY] = registrationKey.trim()
    }

    suspend fun setCredentials(nodeId: String, token: String) = context.dataStore.edit {
        it[NODE_ID] = nodeId
        it[TOKEN] = token
        it[LOCAL_MODE] = false
    }

    suspend fun clearCredentials() = context.dataStore.edit {
        it.remove(NODE_ID)
        it.remove(TOKEN)
    }

    suspend fun setRatePerMinute(value: Int) = context.dataStore.edit { it[RATE] = value.coerceIn(0, 600) }

    suspend fun setLocalMode(enabled: Boolean) = context.dataStore.edit { it[LOCAL_MODE] = enabled }

    suspend fun setServiceEnabled(enabled: Boolean) = context.dataStore.edit { it[ENABLED] = enabled }

    private fun Preferences.toSettings() = Settings(
        backendUrl = this[BACKEND_URL].orEmpty(),
        registrationKey = this[REG_KEY].orEmpty(),
        nodeId = this[NODE_ID],
        token = this[TOKEN],
        ratePerMinute = this[RATE] ?: Settings.DEFAULT_RATE_PER_MINUTE,
        serviceEnabled = this[ENABLED] ?: false,
        localMode = this[LOCAL_MODE] ?: false,
    )

    private companion object {
        val BACKEND_URL = stringPreferencesKey("backend_url")
        val REG_KEY = stringPreferencesKey("registration_key")
        val NODE_ID = stringPreferencesKey("node_id")
        val TOKEN = stringPreferencesKey("token")
        val RATE = intPreferencesKey("rate_per_minute")
        val ENABLED = booleanPreferencesKey("service_enabled")
        val LOCAL_MODE = booleanPreferencesKey("local_mode")
    }
}
