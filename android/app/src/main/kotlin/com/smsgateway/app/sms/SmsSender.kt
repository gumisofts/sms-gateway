package com.smsgateway.app.sms

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.telephony.SmsManager
import androidx.core.content.ContextCompat
import com.smsgateway.app.data.MessageRepository
import com.smsgateway.app.data.OutboundMessage
import com.smsgateway.app.data.RetryPolicy
import java.util.concurrent.atomic.AtomicInteger

/**
 * Hands one claimed (SENDING) message to the platform [SmsManager]. Results arrive asynchronously at
 * [SmsResultReceiver], which feeds them back into [MessageRepository].
 */
class SmsSender(
    private val context: Context,
    private val sims: SimManager,
    private val repository: MessageRepository,
) {
    private val requestCodes = AtomicInteger(1)

    fun hasPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED

    /** SIM a message will go out on and how many SMS parts it needs. */
    data class SendPlan(val subscriptionId: Int?, val parts: Int)

    /** Resolves the SIM and counts parts without sending. Null when that is not possible (send() reports why). */
    fun plan(message: OutboundMessage): SendPlan? {
        val subId = sims.resolveSubscriptionId(message.subscriptionId, message.simSlot)
        if ((message.subscriptionId != null || message.simSlot != null) && subId == null) return null
        return try {
            SendPlan(subId, smsManager(subId).divideMessage(message.body).size.coerceAtLeast(1))
        } catch (_: RuntimeException) {
            null
        }
    }

    /** [message] must already be claimed, i.e. `status == SENDING` with `attempts` incremented. */
    suspend fun send(message: OutboundMessage) {
        val id = message.id
        val attempt = message.attempts
        if (!hasPermission()) {
            repository.onSendError(id, attempt, RetryPolicy.ERR_NO_PERMISSION, "SEND_SMS not granted")
            return
        }
        val subId = sims.resolveSubscriptionId(message.subscriptionId, message.simSlot)
        if ((message.subscriptionId != null || message.simSlot != null) && subId == null) {
            repository.onSendError(id, attempt, RetryPolicy.ERR_SIM_UNAVAILABLE, "Requested SIM not active")
            return
        }
        try {
            val manager = smsManager(subId)
            val parts = manager.divideMessage(message.body)
            val count = parts.size
            // Must be persisted before sending so aggregation knows how many reports to expect.
            repository.setPartsTotal(id, count)
            val sent = ArrayList<PendingIntent>(count)
            val delivered = ArrayList<PendingIntent>(count)
            for (i in 0 until count) {
                sent += pending(SmsResultReceiver.ACTION_SENT, id, attempt, i, count)
                delivered += pending(SmsResultReceiver.ACTION_DELIVERED, id, attempt, i, count)
            }
            if (count == 1) {
                manager.sendTextMessage(message.to, null, parts[0], sent[0], delivered[0])
            } else {
                manager.sendMultipartTextMessage(message.to, null, parts, sent, delivered)
            }
        } catch (e: SecurityException) {
            repository.onSendError(id, attempt, RetryPolicy.ERR_NO_PERMISSION, e.message)
        } catch (e: RuntimeException) {
            // IllegalArgumentException (bad number/empty body) and platform faults: not retryable.
            repository.onSendError(id, attempt, RetryPolicy.ERR_EXCEPTION, e.message ?: e.javaClass.simpleName)
        }
    }

    @Suppress("DEPRECATION")
    private fun smsManager(subscriptionId: Int?): SmsManager {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val base = context.getSystemService(SmsManager::class.java)
            return if (subscriptionId != null) base.createForSubscriptionId(subscriptionId) else base
        }
        return if (subscriptionId != null) SmsManager.getSmsManagerForSubscriptionId(subscriptionId) else SmsManager.getDefault()
    }

    private fun pending(action: String, id: String, attempt: Int, part: Int, count: Int): PendingIntent {
        val kind = if (action == SmsResultReceiver.ACTION_SENT) "sent" else "delivered"
        val intent = Intent(action, Uri.parse("sms-gateway://msg/${Uri.encode(id)}/$attempt/$part/$kind"))
            .setClass(context, SmsResultReceiver::class.java)
            .putExtra(SmsResultReceiver.EXTRA_ID, id)
            .putExtra(SmsResultReceiver.EXTRA_ATTEMPT, attempt)
            .putExtra(SmsResultReceiver.EXTRA_PART, part)
            .putExtra(SmsResultReceiver.EXTRA_COUNT, count)
        // Unique request code per part+kind, on top of the unique data URI above.
        return PendingIntent.getBroadcast(context, requestCodes.getAndIncrement(), intent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
    }
}
