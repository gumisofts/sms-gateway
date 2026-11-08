package com.smsgateway.app.sms

import kotlinx.coroutines.delay

/**
 * Per-key sliding-window limiter measured in SMS parts: at most [perMinute] permits per key in any
 * 60s window. The key is the subscription id a message is sent on (`null` = default SIM), so each SIM
 * has its own independent window. `perMinute <= 0` disables limiting. [perMinute] can be changed at runtime.
 *
 * A request for more permits than [perMinute] is granted once the key's window is empty, so oversized
 * messages never deadlock; it then occupies the whole window until it expires.
 */
class RateLimiter(
    @Volatile var perMinute: Int,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private class Entry(val at: Long, val permits: Int)

    private val windows = HashMap<Int?, ArrayDeque<Entry>>()

    /** Returns 0 and consumes [permits] for [key] if allowed now, otherwise the milliseconds to wait. */
    @Synchronized
    fun tryAcquire(key: Int? = null, permits: Int = 1): Long {
        val limit = perMinute
        if (limit <= 0) return 0
        val need = permits.coerceAtLeast(1)
        val now = clock()
        val window = windows.getOrPut(key) { ArrayDeque() }
        while (window.isNotEmpty() && window.first().at <= now - WINDOW_MS) window.removeFirst()
        var used = window.sumOf { it.permits }
        val capped = minOf(need, limit)
        if (used + capped <= limit) {
            window.addLast(Entry(now, need))
            return 0
        }
        for (e in window) {
            used -= e.permits
            if (used + capped <= limit) return (e.at + WINDOW_MS - now).coerceAtLeast(1)
        }
        return 1 // unreachable: an empty window always admits
    }

    /** Suspends until the permits are free. Cancellation-safe: permits are only consumed when granted. */
    suspend fun acquire(key: Int? = null, permits: Int = 1) {
        while (true) {
            val wait = tryAcquire(key, permits)
            if (wait == 0L) return
            delay(wait)
        }
    }

    companion object {
        const val WINDOW_MS = 60_000L
    }
}
