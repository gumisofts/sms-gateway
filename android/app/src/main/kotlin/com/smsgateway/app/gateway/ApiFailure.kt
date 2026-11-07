package com.smsgateway.app.gateway

import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.UnknownHostException
import java.net.UnknownServiceException
import javax.net.ssl.SSLException

/** Why a REST call to the backend failed. UI code maps these to messages; no string matching needed. */
sealed interface ApiFailure {
    /** The URL is unusable (blank, bad scheme, bad host). */
    data object InvalidUrl : ApiFailure
    /** DNS lookup failed: the host name does not exist or there is no connectivity. */
    data object UnknownHost : ApiFailure
    data object Timeout : ApiFailure
    data object ConnectionRefused : ApiFailure
    /** Any other transport failure (no route, network down, reset...). */
    data object Unreachable : ApiFailure
    /** Cleartext http:// blocked by the network security policy (release builds). */
    data object CleartextBlocked : ApiFailure
    data object TlsError : ApiFailure
    /** 401/403: wrong or missing registration key. */
    data object KeyRejected : ApiFailure
    /** 404: the server is reachable but does not expose the gateway API at this URL. */
    data object NotFound : ApiFailure
    data class ServerError(val code: Int) : ApiFailure
    data class UnexpectedStatus(val code: Int) : ApiFailure
    /** 2xx but the body is not a valid register response. */
    data object MalformedResponse : ApiFailure

    companion object {
        fun fromHttpStatus(code: Int): ApiFailure = when (code) {
            401, 403 -> KeyRejected
            404 -> NotFound
            in 500..599 -> ServerError(code)
            else -> UnexpectedStatus(code)
        }

        /** Classifies a transport exception by type (and cause chain), not by its message, except for the cleartext case. */
        fun fromThrowable(t: Throwable): ApiFailure {
            val chain = generateSequence(t) { it.cause }.take(8).toList()
            return when {
                chain.any { it is UnknownServiceException || it.message?.contains("cleartext", ignoreCase = true) == true } ->
                    CleartextBlocked
                chain.any { it is SSLException } -> TlsError
                chain.any { it is UnknownHostException } -> UnknownHost
                chain.any { it is InterruptedIOException } -> Timeout // SocketTimeoutException extends it
                chain.any { it is ConnectException && it.message?.contains("refused", ignoreCase = true) == true } ->
                    ConnectionRefused
                else -> Unreachable
            }
        }
    }
}
