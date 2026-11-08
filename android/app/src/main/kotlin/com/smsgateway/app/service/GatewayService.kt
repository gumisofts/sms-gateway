package com.smsgateway.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.smsgateway.app.GatewayApp
import com.smsgateway.app.MainActivity
import com.smsgateway.app.R
import com.smsgateway.app.gateway.ConnectionState
import com.smsgateway.app.gateway.GatewayClient
import com.smsgateway.app.gateway.GatewayStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Foreground service that keeps the backend connection open and drains the SMS queue. */
class GatewayService : Service() {
    private var scope: CoroutineScope? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var client: GatewayClient? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (scope != null) {
            if (intent?.action == ACTION_RECONNECT) client?.reconnectNow()
            return START_STICKY
        }
        val container = (application as GatewayApp).container
        createChannel()
        ServiceCompat.startForeground(
            this, NOTIFICATION_ID, buildNotification("Starting..."),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_REMOTE_MESSAGING else 0,
        )
        wakeLock = getSystemService(PowerManager::class.java)
            .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "smsgateway:service")
            .apply { acquire() } // released in onDestroy; keeps the socket + sender alive in doze-lite

        val s = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        scope = s
        s.launch { container.smsLimit.apply() } // no-op unless WRITE_SECURE_SETTINGS was granted via adb
        val client = container.newGatewayClient()
        this.client = client
        s.launch { client.run() }
        s.launch { client.state.collect { st -> _status.update { it.next(st, System.currentTimeMillis()) } } }
        s.launch { container.newDispatcher().run() }
        s.launch { container.settings.flow.collect { container.rateLimiter.perMinute = it.ratePerMinute } }
        s.launch {
            combine(client.state, container.messages.observeQueueDepth()) { state, depth -> NotificationText.describe(state, depth) }
                .collect { text ->
                    getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, buildNotification(text))
                }
        }
        s.launch { container.messages.pruneFinished() }
        _running.value = true
        return START_STICKY
    }

    override fun onDestroy() {
        scope?.cancel()
        scope = null
        wakeLock?.takeIf { it.isHeld }?.release()
        _running.value = false
        client = null
        _status.update { it.next(ConnectionState.Stopped, System.currentTimeMillis()) }
        super.onDestroy()
    }

    private fun createChannel() {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.notification_channel_name), NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun buildNotification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    companion object {
        private const val CHANNEL_ID = "gateway_service"
        private const val NOTIFICATION_ID = 1

        private val _running = MutableStateFlow(false)
        val running: StateFlow<Boolean> = _running

        private val _status = MutableStateFlow(GatewayStatus())
        /** Connection state with timestamps; the UI derives countdowns / durations from these. */
        val status: StateFlow<GatewayStatus> = _status

        private const val ACTION_RECONNECT = "com.smsgateway.app.action.RECONNECT"

        /** Skips the reconnect backoff wait. Only meaningful while the service runs. */
        fun reconnectNow(context: Context) {
            if (!_running.value) return
            context.startService(Intent(context, GatewayService::class.java).setAction(ACTION_RECONNECT))
        }

        fun start(context: Context) {
            ContextCompat.startForegroundService(context, Intent(context, GatewayService::class.java))
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, GatewayService::class.java))
        }
    }
}
