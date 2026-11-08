package com.smsgateway.app.gateway

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Build
import android.telephony.TelephonyManager
import com.smsgateway.app.sms.SimManager
import com.smsgateway.app.sms.SmsLimitManager

/** Reads device facts for registration and heartbeats. */
class DeviceStatusProvider(
    private val context: Context,
    private val sims: SimManager,
    private val smsLimit: SmsLimitManager,
) {

    fun appVersion(): String = try {
        context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "unknown"
    } catch (_: Exception) {
        "unknown"
    }

    fun registerRequest(previousNodeId: String?, registrationKey: String?): RegisterRequest = RegisterRequest(
        deviceName = (Build.MODEL ?: "android"),
        manufacturer = Build.MANUFACTURER.orEmpty(),
        model = Build.MODEL.orEmpty(),
        androidVersion = Build.VERSION.RELEASE.orEmpty(),
        sdkInt = Build.VERSION.SDK_INT,
        appVersion = appVersion(),
        sims = sims.activeSims(),
        previousNodeId = previousNodeId,
        registrationKey = registrationKey?.ifBlank { null },
    )

    fun heartbeat(queueDepth: Int, ratePerMinute: Int, now: Long = System.currentTimeMillis()): NodeMessage.Heartbeat {
        val battery: Intent? = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = battery?.getIntExtra(BatteryManager.EXTRA_SCALE, 100) ?: 100
        val status = battery?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val charging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        return NodeMessage.Heartbeat(
            batteryPercent = if (level >= 0 && scale > 0) level * 100 / scale else -1,
            charging = charging,
            signalLevel = signalLevel(),
            sims = sims.activeSims(),
            queueDepth = queueDepth,
            ratePerMinute = ratePerMinute,
            timestamp = now,
            smsLimit = smsLimit.status().let { SmsLimitInfo(it.raised, it.maxCount, it.intervalMs) },
        )
    }

    private fun signalLevel(): Int? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) return null
        return try {
            context.getSystemService(TelephonyManager::class.java)?.signalStrength?.level
        } catch (_: SecurityException) {
            null
        }
    }
}
