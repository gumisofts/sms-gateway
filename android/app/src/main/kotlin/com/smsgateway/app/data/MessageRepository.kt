package com.smsgateway.app.data

import androidx.room.withTransaction
import com.smsgateway.app.sms.DeliveryClass
import kotlinx.coroutines.flow.Flow
import java.util.UUID

enum class EnqueueResult { ACCEPTED, DUPLICATE, QUEUE_FULL }

/**
 * Owns all state transitions of [OutboundMessage]. Every transition runs in a Room transaction so
 * concurrent broadcast results for multipart messages are aggregated safely.
 *
 * Lifecycle: QUEUED -> SENDING -> SENT -> DELIVERED, with SENDING -> QUEUED (retry) and
 * SENDING/SENT -> FAILED as the other edges.
 */
class MessageRepository(
    private val db: AppDatabase,
    private val retryPolicy: RetryPolicy = RetryPolicy(),
    private val clock: () -> Long = System::currentTimeMillis,
    private val maxQueueDepth: Int = 5_000,
) {
    private val dao = db.messageDao()

    fun observeRecent(limit: Int = 50): Flow<List<OutboundMessage>> = dao.observeRecent(limit)
    fun observeEarliestQueued(): Flow<Long?> = dao.observeEarliestQueued()
    fun observeUnreported(): Flow<List<OutboundMessage>> = dao.observeUnreported()
    fun observeQueueDepth(): Flow<Int> = dao.observeQueueDepth()
    suspend fun queueDepth(): Int = dao.queueDepth()
    suspend fun nextDue(): OutboundMessage? = dao.nextDue(clock())
    suspend fun markReported(id: String, status: MessageStatus) = dao.markReported(id, status)

    /** Test-send path: never reported to a backend. */
    suspend fun enqueueLocal(to: String, body: String, subscriptionId: Int?, simSlot: Int?): String {
        val now = clock()
        val id = "local-" + UUID.randomUUID()
        dao.insert(
            OutboundMessage(
                id = id, to = to, body = body, subscriptionId = subscriptionId, simSlot = simSlot,
                reportable = false, reportedStatus = null, nextAttemptAt = now, createdAt = now, updatedAt = now,
            ),
        )
        return id
    }

    /** Backend job. Idempotent on [id]: a redelivered job is reported as DUPLICATE and not re-queued. */
    suspend fun enqueueRemote(id: String, to: String, body: String, simSlot: Int?, subscriptionId: Int?): EnqueueResult {
        if (dao.get(id) != null) return EnqueueResult.DUPLICATE
        if (dao.queueDepth() >= maxQueueDepth) return EnqueueResult.QUEUE_FULL
        val now = clock()
        val inserted = dao.insert(
            OutboundMessage(
                id = id, to = to, body = body, simSlot = simSlot, subscriptionId = subscriptionId,
                nextAttemptAt = now, createdAt = now, updatedAt = now,
            ),
        )
        return if (inserted == -1L) EnqueueResult.DUPLICATE else EnqueueResult.ACCEPTED
    }

    /** Atomically moves a QUEUED message to SENDING (attempts + 1). Returns the fresh row or null. */
    suspend fun claim(id: String): OutboundMessage? = db.withTransaction {
        if (dao.claim(id, clock()) == 1) dao.get(id) else null
    }

    suspend fun setPartsTotal(id: String, total: Int) = db.withTransaction {
        dao.get(id)?.let { dao.update(it.copy(partsTotal = total, updatedAt = clock())) }
    }

    /** The send call itself failed synchronously (exception / no permission / bad SIM). */
    suspend fun onSendError(id: String, attempt: Int, code: Int, message: String?) = db.withTransaction {
        val m = dao.get(id)?.takeIf { it.status == MessageStatus.SENDING && it.attempts == attempt } ?: return@withTransaction
        dao.update(resolveFailure(m.copy(errorCode = code, errorMessage = message), code))
    }

    /** One part of a (multipart) message got its "sent" broadcast. */
    suspend fun onPartSent(id: String, attempt: Int, ok: Boolean, code: Int) = db.withTransaction {
        val m = dao.get(id)?.takeIf { it.status == MessageStatus.SENDING && it.attempts == attempt } ?: return@withTransaction
        var u = m.copy(
            partsReported = m.partsReported + 1,
            partsOk = m.partsOk + if (ok) 1 else 0,
            errorCode = m.errorCode ?: if (ok) null else code,
            updatedAt = clock(),
        )
        if (u.partsReported >= u.partsTotal.coerceAtLeast(1)) {
            u = if (u.partsOk >= u.partsTotal.coerceAtLeast(1)) {
                val sent = u.copy(status = MessageStatus.SENT, errorCode = null, errorMessage = null)
                if (sent.partsDelivered >= sent.partsTotal) sent.copy(status = MessageStatus.DELIVERED) else sent
            } else {
                resolveFailure(u.copy(errorMessage = RetryPolicy.describe(u.errorCode ?: -2)), u.errorCode ?: RetryPolicy.ERR_EXCEPTION)
            }
        }
        dao.update(u)
    }

    /** One part's delivery report arrived. */
    suspend fun onPartDelivered(id: String, attempt: Int, cls: DeliveryClass) = db.withTransaction {
        val m = dao.get(id)?.takeIf { it.attempts == attempt } ?: return@withTransaction
        val now = clock()
        when (cls) {
            DeliveryClass.PENDING -> Unit
            DeliveryClass.FAILED -> if (m.status == MessageStatus.SENT || m.status == MessageStatus.SENDING) {
                dao.update(m.copy(status = MessageStatus.FAILED, errorMessage = "DELIVERY_FAILED", updatedAt = now))
            }
            DeliveryClass.DELIVERED -> {
                val delivered = m.partsDelivered + 1
                val done = m.status == MessageStatus.SENT && delivered >= m.partsTotal.coerceAtLeast(1)
                dao.update(m.copy(partsDelivered = delivered, status = if (done) MessageStatus.DELIVERED else m.status, updatedAt = now))
            }
        }
    }

    /**
     * Messages stuck in SENDING (process died before the result broadcast). They are failed rather
     * than retried: the SMS may already have left the phone and a resend would duplicate it.
     */
    suspend fun recoverStale(olderThanMs: Long = 5 * 60_000): Int = db.withTransaction {
        val now = clock()
        val stale = dao.staleSending(now - olderThanMs)
        stale.forEach {
            dao.update(it.copy(status = MessageStatus.FAILED, errorCode = RetryPolicy.ERR_STALE, errorMessage = "STALE_SENDING", updatedAt = now))
        }
        stale.size
    }

    suspend fun pruneFinished(olderThanMs: Long = 7 * 24 * 3_600_000L) = dao.deleteFinishedBefore(clock() - olderThanMs)

    private fun resolveFailure(m: OutboundMessage, code: Int): OutboundMessage {
        val now = clock()
        // Never retry if any part already left the phone: that would duplicate text for the recipient.
        val retry = m.partsOk == 0 && retryPolicy.shouldRetry(code, m.attempts)
        return if (retry) {
            m.copy(status = MessageStatus.QUEUED, nextAttemptAt = now + retryPolicy.nextDelayMs(m.attempts), updatedAt = now)
        } else {
            m.copy(status = MessageStatus.FAILED, updatedAt = now)
        }
    }
}
