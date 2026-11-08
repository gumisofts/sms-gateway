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

    @Test
    fun keysAreIndependent() {
        val rl = RateLimiter(2) { now }
        assertEquals(0L, rl.tryAcquire(1, 2))
        assertEquals(60_000L, rl.tryAcquire(1, 1))
        assertEquals(0L, rl.tryAcquire(2, 2))
        assertEquals(0L, rl.tryAcquire(null, 1))
        assertEquals(0L, rl.tryAcquire(null, 1))
        assertEquals(60_000L, rl.tryAcquire(null, 1))
    }

    @Test
    fun multiPermitConsumesPartsAndWaitsForEnoughToFree() {
        val rl = RateLimiter(5) { now }
        assertEquals(0L, rl.tryAcquire(1, 2)); now += 10_000
        assertEquals(0L, rl.tryAcquire(1, 2)); now += 10_000
        // 4 used, need 3: must wait for the first entry (t0) to expire -> 40s left
        assertEquals(40_000L, rl.tryAcquire(1, 3))
        assertEquals(0L, rl.tryAcquire(1, 1))
        now += 40_000 // t0 entry expired; 3 used (2 + 1), need 3 -> wait for the t10 entry
        assertEquals(10_000L, rl.tryAcquire(1, 3))
        now += 10_000
        assertEquals(0L, rl.tryAcquire(1, 3))
    }

    @Test
    fun oversizedMessageAllowedWhenWindowEmptyThenBlocksWindow() {
        val rl = RateLimiter(3) { now }
        assertEquals(0L, rl.tryAcquire(1, 10))
        assertEquals(60_000L, rl.tryAcquire(1, 1))
        now += 60_000
        assertEquals(0L, rl.tryAcquire(1, 1))
        // oversized waits for a non-empty window to drain completely
        assertEquals(60_000L, rl.tryAcquire(1, 10))
    }

    @Test
    fun unlimitedIgnoresPermits() {
        val rl = RateLimiter(0) { now }
        repeat(10) { assertEquals(0L, rl.tryAcquire(it, 1000)) }
    }
}
