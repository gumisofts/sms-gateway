package com.smsgateway.app.sms

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.SmsMessage
import com.smsgateway.app.GatewayApp
import kotlinx.coroutines.launch

/** Manifest-registered receiver for the sent / delivered PendingIntents created by [SmsSender]. */
class SmsResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(EXTRA_ID) ?: return
        val attempt = intent.getIntExtra(EXTRA_ATTEMPT, -1)
        val resultCode = resultCode
        val container = (context.applicationContext as GatewayApp).container
        val pendingResult = goAsync()
        container.appScope.launch {
            try {
                when (intent.action) {
                    ACTION_SENT -> container.messages.onPartSent(id, attempt, resultCode == Activity.RESULT_OK, resultCode)
                    ACTION_DELIVERED -> container.messages.onPartDelivered(id, attempt, deliveryClass(intent, resultCode))
                }
            } finally {
                pendingResult.finish()
            }
        }
    }

    private fun deliveryClass(intent: Intent, resultCode: Int): DeliveryClass {
        if (resultCode != Activity.RESULT_OK) return DeliveryClass.FAILED
        val pdu = intent.getByteArrayExtra("pdu") ?: return DeliveryClass.DELIVERED
        return try {
            DeliveryClass.fromTpStatus(SmsMessage.createFromPdu(pdu, intent.getStringExtra("format")).status)
        } catch (_: Exception) {
            DeliveryClass.DELIVERED
        }
    }

    companion object {
        const val ACTION_SENT = "com.smsgateway.app.SMS_SENT"
        const val ACTION_DELIVERED = "com.smsgateway.app.SMS_DELIVERED"
        const val EXTRA_ID = "id"
        const val EXTRA_ATTEMPT = "attempt"
        const val EXTRA_PART = "part"
        const val EXTRA_COUNT = "count"
    }
}
