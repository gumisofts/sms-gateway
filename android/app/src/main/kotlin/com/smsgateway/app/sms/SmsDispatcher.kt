package com.smsgateway.app.sms

import com.smsgateway.app.data.MessageRepository
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.withContext

/**
 * Drains the persistent queue: waits until the earliest QUEUED message is due, respects the
 * [RateLimiter], claims the message atomically and passes it to [SmsSender]. Runs until cancelled.
 */
class SmsDispatcher(
    private val repository: MessageRepository,
    private val sender: SmsSender,
    private val rateLimiter: RateLimiter,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    suspend fun run() {
        repository.recoverStale()
        // Re-evaluates whenever the queue changes; collectLatest cancels a pending rate-limit wait.
        repository.observeEarliestQueued().distinctUntilChanged().collectLatest { earliest ->
            if (earliest == null) return@collectLatest
            val wait = earliest - clock()
            if (wait > 0) delay(wait)
            drain()
        }
    }

    private suspend fun drain() {
        while (true) {
            val next = repository.nextDue() ?: return
            if (!sender.hasPermission()) {
                // Park until permission is granted; the queue keeps its state.
                delay(PERMISSION_RECHECK_MS)
                continue
            }
            rateLimiter.acquire() // cancellable; nothing is claimed yet
            // From here on do not abandon a claimed message half way.
            withContext(NonCancellable) {
                repository.claim(next.id)?.let { sender.send(it) }
            }
        }
    }

    private companion object {
        const val PERMISSION_RECHECK_MS = 10_000L
    }
}
