package com.smsgateway.app.sms

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.telephony.SubscriptionManager
import androidx.core.content.ContextCompat
import kotlinx.serialization.Serializable

@Serializable
data class SimInfo(
    val slot: Int,
    val subscriptionId: Int,
    val carrier: String,
)

/** Lists active SIM subscriptions. Returns an empty list without READ_PHONE_STATE. */
class SimManager(private val context: Context) {

    @SuppressLint("MissingPermission")
    fun activeSims(): List<SimInfo> {
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_STATE) != PackageManager.PERMISSION_GRANTED) {
            return emptyList()
        }
        val sm = context.getSystemService(SubscriptionManager::class.java) ?: return emptyList()
        return try {
            sm.activeSubscriptionInfoList.orEmpty()
                .map { SimInfo(it.simSlotIndex, it.subscriptionId, (it.carrierName ?: it.displayName)?.toString().orEmpty()) }
                .sortedBy { it.slot }
        } catch (_: SecurityException) {
            emptyList()
        }
    }

    /** Resolves a message's SIM choice. Returns null for "use the system default SIM". */
    fun resolveSubscriptionId(subscriptionId: Int?, slot: Int?): Int? {
        if (subscriptionId != null) return subscriptionId
        if (slot == null) return null
        return activeSims().firstOrNull { it.slot == slot }?.subscriptionId
    }
}
