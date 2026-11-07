package com.smsgateway.app.gateway

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/** Thrown by [GatewayApi]; [failure] says what went wrong so callers need no string matching. */
class GatewayApiException(val failure: ApiFailure, cause: Throwable? = null) :
    IOException("Gateway API failure: $failure" + (cause?.message?.let { " ($it)" } ?: ""), cause)

/** REST part of the protocol. */
class GatewayApi(private val http: OkHttpClient, private val json: Json = GatewayJson.instance) {

    suspend fun register(baseUrl: String, request: RegisterRequest): RegisterResponse = withContext(Dispatchers.IO) {
        val url = GatewayEndpoints.normalizeBaseUrl(baseUrl) ?: throw GatewayApiException(ApiFailure.InvalidUrl)
        val body = json.encodeToString(RegisterRequest.serializer(), request).toRequestBody(JSON_MEDIA)
        val req = try {
            Request.Builder().url(GatewayEndpoints.register(url)).post(body).build()
        } catch (e: IllegalArgumentException) {
            throw GatewayApiException(ApiFailure.InvalidUrl, e)
        }
        val text = try {
            http.newCall(req).execute().use { resp ->
                val text = resp.body.string()
                if (!resp.isSuccessful) throw GatewayApiException(ApiFailure.fromHttpStatus(resp.code))
                text
            }
        } catch (e: GatewayApiException) {
            throw e
        } catch (e: IOException) {
            throw GatewayApiException(ApiFailure.fromThrowable(e), e)
        }
        try {
            json.decodeFromString(RegisterResponse.serializer(), text)
        } catch (e: SerializationException) {
            throw GatewayApiException(ApiFailure.MalformedResponse, e)
        } catch (e: IllegalArgumentException) {
            throw GatewayApiException(ApiFailure.MalformedResponse, e)
        }
    }

    private companion object {
        val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    }
}
