package com.smsgateway.app.sms

import kotlinx.coroutines.delay

/**
 * Sliding-window limiter: at most [perMinute] sends in any 60s window. `perMinute <= 0` disables limiting.
 * [perMinute] can be changed at runtime.
 */
class RateLimiter(
    @Volatile var perMinute: Int,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val stamps = ArrayDeque<Long>()

    /** Returns 0 and records a send if allowed now, otherwise the milliseconds to wait. */
    @Synchronized
    fun tryAcquire(): Long {
        val limit = perMinute
        if (limit <= 0) return 0
        val now = clock()
        while (stamps.isNotEmpty() && stamps.first() <= now - WINDOW_MS) stamps.removeFirst()
        if (stamps.size < limit) {
            stamps.addLast(now)
            return 0
        }
        return (stamps.first() + WINDOW_MS - now).coerceAtLeast(1)
    }

    /** Suspends until a slot is free. Cancellation-safe: a slot is only consumed when granted. */
    suspend fun acquire() {
        while (true) {
            val wait = tryAcquire()
            if (wait == 0L) return
            delay(wait)
        }
    }

    companion object {
        const val WINDOW_MS = 60_000L
    }
}
