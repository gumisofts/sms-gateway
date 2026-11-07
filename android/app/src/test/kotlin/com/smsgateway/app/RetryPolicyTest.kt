package com.smsgateway.app

import com.smsgateway.app.data.RetryPolicy
import com.smsgateway.app.sms.DeliveryClass
import com.smsgateway.app.util.Backoff
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class RetryPolicyTest {
    @Test
    fun transientClassification() {
        val p = RetryPolicy()
        assertTrue(p.isTransient(RetryPolicy.NO_SERVICE))
        assertTrue(p.isTransient(RetryPolicy.RADIO_OFF))
        assertFalse(p.isTransient(RetryPolicy.NULL_PDU))
        assertFalse(p.isTransient(RetryPolicy.ERR_NO_PERMISSION))
    }

    @Test
    fun givesUpAfterMaxAttempts() {
        val p = RetryPolicy(maxAttempts = 3)
        assertTrue(p.shouldRetry(RetryPolicy.NO_SERVICE, 1))
        assertTrue(p.shouldRetry(RetryPolicy.NO_SERVICE, 2))
        assertFalse(p.shouldRetry(RetryPolicy.NO_SERVICE, 3))
        assertFalse(p.shouldRetry(RetryPolicy.NULL_PDU, 1))
    }

    @Test
    fun backoffGrowsAndIsCapped() {
        val noJitter = { a: Int -> Backoff.delayMs(a, 1_000, 10_000, jitter = 0.0) }
        assertEquals(listOf(1_000L, 2_000L, 4_000L, 8_000L, 10_000L, 10_000L), (1..6).map(noJitter))
    }

    @Test
    fun jitterStaysWithinBounds() {
        val r = Random(42)
        repeat(1000) {
            val d = Backoff.delayMs(3, 1_000, 60_000, jitter = 0.5, random = r)
            assertTrue("d=$d", d in 2_000..4_000)
        }
    }

    @Test
    fun policyDelayIncreases() {
        val p = RetryPolicy(baseMs = 1_000, maxMs = 1_000_000, random = Random(1))
        assertTrue(p.nextDelayMs(4) > p.nextDelayMs(1))
    }

    @Test
    fun deliveryStatusClassification() {
        assertEquals(DeliveryClass.DELIVERED, DeliveryClass.fromTpStatus(0))
        assertEquals(DeliveryClass.PENDING, DeliveryClass.fromTpStatus(0x22))
        assertEquals(DeliveryClass.FAILED, DeliveryClass.fromTpStatus(0x41))
        assertEquals(DeliveryClass.FAILED, DeliveryClass.fromTpStatus(0x61))
    }
}
