package com.smsgateway.app

import android.app.Application
import android.content.Context
import androidx.room.Room
import com.smsgateway.app.data.AppDatabase
import com.smsgateway.app.data.MessageRepository
import com.smsgateway.app.data.RetryPolicy
import com.smsgateway.app.data.SettingsStore
import com.smsgateway.app.gateway.DeviceStatusProvider
import com.smsgateway.app.gateway.GatewayApi
import com.smsgateway.app.gateway.GatewayClient
import com.smsgateway.app.gateway.NodeRegistrar
import com.smsgateway.app.sms.RateLimiter
import com.smsgateway.app.sms.SimManager
import com.smsgateway.app.sms.SmsDispatcher
import com.smsgateway.app.sms.SmsLimitManager
import com.smsgateway.app.sms.SmsSender
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

class GatewayApp : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
    }
}

/** Manual DI: one place that wires every long-lived object. */
class AppContainer(context: Context) {
    val appContext: Context = context.applicationContext

    /** Process-wide scope for work that must outlive a single broadcast / screen. */
    val appScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    val settings = SettingsStore(appContext)
    val simManager = SimManager(appContext)
    val smsLimit = SmsLimitManager(appContext)
    val deviceStatus = DeviceStatusProvider(appContext, simManager, smsLimit)

    private val database: AppDatabase by lazy {
        Room.databaseBuilder(appContext, AppDatabase::class.java, "gateway.db").build()
    }
    val messages: MessageRepository by lazy { MessageRepository(database, RetryPolicy()) }

    val rateLimiter = RateLimiter(perMinute = 20)
    val smsSender = SmsSender(appContext, simManager, messages)

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // WebSocket: liveness is handled by pings
        .pingInterval(25, TimeUnit.SECONDS)
        .build()
    val api = GatewayApi(httpClient)
    val registrar = NodeRegistrar(settings, api, deviceStatus)

    fun newDispatcher() = SmsDispatcher(messages, smsSender, rateLimiter)
    fun newGatewayClient() = GatewayClient(settings, httpClient, messages, deviceStatus)
}
