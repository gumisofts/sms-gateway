package com.smsgateway.app

import com.smsgateway.app.sms.RateLimiter
import org.junit.Assert.assertEquals
import org.junit.Test

class RateLimiterTest {
    private var now = 1_000_000L

    @Test
    fun allowsUpToLimitThenReportsWait() {
        val rl = RateLimiter(3) { now }
        repeat(3) { assertEquals(0L, rl.tryAcquire()); now += 1_000 }
        // oldest stamp was at t0; window frees at t0+60s; now = t0+3s
        assertEquals(57_000L, rl.tryAcquire())
    }

    @Test
    fun slotFreesAfterWindow() {
        val rl = RateLimiter(1) { now }
        assertEquals(0L, rl.tryAcquire())
        now += 59_999
        assertEquals(1L, rl.tryAcquire())
        now += 1
        assertEquals(0L, rl.tryAcquire())
    }

    @Test
    fun zeroMeansUnlimitedAndLimitIsMutable() {
        val rl = RateLimiter(0) { now }
        repeat(100) { assertEquals(0L, rl.tryAcquire()) }
        rl.perMinute = 1
        // stamps from the unlimited phase were not recorded, so one more is allowed, then blocked
        assertEquals(0L, rl.tryAcquire())
        assertEquals(60_000L, rl.tryAcquire())
    }
}
