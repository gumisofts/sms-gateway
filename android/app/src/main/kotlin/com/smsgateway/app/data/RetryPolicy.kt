package com.smsgateway.app.data

import com.smsgateway.app.util.Backoff
import kotlin.random.Random

/** Decides whether a failed send is worth retrying and when. Pure; no Android dependencies. */
class RetryPolicy(
    val maxAttempts: Int = 5,
    private val baseMs: Long = 30_000,
    private val maxMs: Long = 15 * 60_000,
    private val random: Random = Random.Default,
) {
    /** True for failures that are expected to clear on their own (no signal, radio off, modem hiccup). */
    fun isTransient(errorCode: Int): Boolean = errorCode in TRANSIENT_CODES

    /** [attemptsMade] counts attempts already performed, including the one that just failed. */
    fun shouldRetry(errorCode: Int, attemptsMade: Int): Boolean =
        isTransient(errorCode) && attemptsMade < maxAttempts

    /** Delay before the next attempt after [attemptsMade] attempts. */
    fun nextDelayMs(attemptsMade: Int): Long =
        Backoff.delayMs(attemptsMade.coerceAtLeast(1), baseMs, maxMs, random = random)

    companion object {
        // Literals mirror SmsManager.RESULT_ERROR_* so the policy stays unit-testable on the JVM.
        const val GENERIC_FAILURE = 1
        const val RADIO_OFF = 2
        const val NULL_PDU = 3
        const val NO_SERVICE = 4
        const val LIMIT_EXCEEDED = 5
        const val RADIO_NOT_AVAILABLE = 9
        const val NETWORK_REJECT = 10
        const val MODEM_ERROR = 16
        const val NETWORK_ERROR = 17
        const val INTERNAL_ERROR = 21
        const val NO_RESOURCES = 22

        /** Local (non-telephony) error codes. */
        const val ERR_NO_PERMISSION = -1
        const val ERR_EXCEPTION = -2
        const val ERR_STALE = -3
        const val ERR_SIM_UNAVAILABLE = -4

        val TRANSIENT_CODES = setOf(
            GENERIC_FAILURE, RADIO_OFF, NO_SERVICE, LIMIT_EXCEEDED, RADIO_NOT_AVAILABLE,
            NETWORK_REJECT, MODEM_ERROR, NETWORK_ERROR, INTERNAL_ERROR, NO_RESOURCES,
        )

        fun describe(code: Int): String = when (code) {
            GENERIC_FAILURE -> "GENERIC_FAILURE"
            RADIO_OFF -> "RADIO_OFF"
            NULL_PDU -> "NULL_PDU"
            NO_SERVICE -> "NO_SERVICE"
            LIMIT_EXCEEDED -> "LIMIT_EXCEEDED (Android SMS limit; raise sms_outgoing_check_max_count)"
            6 -> "FDN_CHECK_FAILURE"
            7, 8 -> "SHORT_CODE_NOT_ALLOWED"
            RADIO_NOT_AVAILABLE -> "RADIO_NOT_AVAILABLE"
            NETWORK_REJECT -> "NETWORK_REJECT"
            MODEM_ERROR -> "MODEM_ERROR"
            NETWORK_ERROR -> "NETWORK_ERROR"
            ERR_NO_PERMISSION -> "NO_PERMISSION"
            ERR_EXCEPTION -> "EXCEPTION"
            ERR_STALE -> "STALE_SENDING"
            ERR_SIM_UNAVAILABLE -> "SIM_UNAVAILABLE"
            else -> "ERROR_$code"
        }
    }
}
