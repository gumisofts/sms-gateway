package com.smsgateway.app.util

import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

/** Pure exponential-backoff helper shared by the SMS retry policy and the WebSocket reconnect loop. */
object Backoff {
    /**
     * Delay before retry number [attempt] (1-based): `base * multiplier^(attempt-1)` capped at [maxMs],
     * then reduced by up to [jitter] (0..1) of itself. The result is always within
     * `[exp * (1 - jitter), exp]`, so it never exceeds [maxMs].
     */
    fun delayMs(
        attempt: Int,
        baseMs: Long,
        maxMs: Long,
        multiplier: Double = 2.0,
        jitter: Double = 0.25,
        random: Random = Random.Default,
    ): Long {
        require(attempt >= 1) { "attempt is 1-based" }
        val exp = min(maxMs.toDouble(), baseMs * multiplier.pow(attempt - 1))
        return (exp * (1.0 - jitter * random.nextDouble())).toLong().coerceAtLeast(0)
    }
}
