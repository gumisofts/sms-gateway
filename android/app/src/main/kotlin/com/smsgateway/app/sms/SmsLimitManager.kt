package com.smsgateway.app.sms

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Settings
import androidx.core.content.ContextCompat

/** [raised] null = unknown (settings unreadable). Unset keys mean Android's default of 30 per 30 min. */
data class SmsLimitStatus(val raised: Boolean?, val maxCount: Int?, val intervalMs: Long?)

/**
 * Android throttles each app's outgoing SMS (SmsUsageMonitor). The limit lives in hidden
 * Settings.Global keys that can only be written with WRITE_SECURE_SETTINGS, which an operator grants
 * once over adb: `adb shell pm grant com.smsgateway.app android.permission.WRITE_SECURE_SETTINGS`.
 */
class SmsLimitManager(private val context: Context) {
    @Volatile private var applied = false

    fun hasWriteSecureSettings(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_SECURE_SETTINGS) == PackageManager.PERMISSION_GRANTED

    /** Raises the limit if permitted. Returns true when both values were written. */
    fun apply(): Boolean {
        if (!hasWriteSecureSettings()) return false
        return try {
            val r = context.contentResolver
            val ok = Settings.Global.putInt(r, KEY_MAX_COUNT, RAISED_MAX_COUNT) &&
                Settings.Global.putInt(r, KEY_INTERVAL_MS, RAISED_INTERVAL_MS)
            if (ok) applied = true
            ok
        } catch (_: Exception) {
            false
        }
    }

    fun status(): SmsLimitStatus {
        var maxCount: Int? = null
        var intervalMs: Long? = null
        var readable = true
        try {
            val r = context.contentResolver
            maxCount = Settings.Global.getString(r, KEY_MAX_COUNT)?.toIntOrNull()
            intervalMs = Settings.Global.getString(r, KEY_INTERVAL_MS)?.toLongOrNull()
        } catch (_: Exception) {
            readable = false // hidden keys can be unreadable on newer targetSdk
        }
        val raised = when {
            applied -> true
            !readable -> null
            else -> (maxCount ?: 0) >= RAISED_THRESHOLD
        }
        return SmsLimitStatus(raised, maxCount, intervalMs)
    }

    private companion object {
        const val KEY_MAX_COUNT = "sms_outgoing_check_max_count"
        const val KEY_INTERVAL_MS = "sms_outgoing_check_interval_ms"
        const val RAISED_MAX_COUNT = 100_000
        const val RAISED_INTERVAL_MS = 60_000
        const val RAISED_THRESHOLD = 1000
    }
}
