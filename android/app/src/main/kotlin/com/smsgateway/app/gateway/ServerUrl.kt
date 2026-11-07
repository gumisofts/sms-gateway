package com.smsgateway.app.gateway

import java.net.URI
import java.net.URISyntaxException

enum class UrlProblem { Empty, UnsupportedScheme, InvalidHost }

sealed interface UrlParseResult {
    /** [url] is canonical (scheme + authority + optional path, no trailing slash). */
    data class Valid(val url: String, val schemeAssumed: Boolean) : UrlParseResult
    data class Invalid(val problem: UrlProblem) : UrlParseResult
}

/** Parses what a user types into the "Server URL" field. Pure JVM. */
object ServerUrl {
    fun parse(input: String): UrlParseResult {
        val s = input.trim()
        if (s.isEmpty()) return UrlParseResult.Invalid(UrlProblem.Empty)

        val sep = s.indexOf("://")
        val scheme: String
        val rest: String
        val assumed: Boolean
        if (sep >= 0) {
            scheme = s.substring(0, sep).lowercase()
            if (scheme != "http" && scheme != "https") return UrlParseResult.Invalid(UrlProblem.UnsupportedScheme)
            rest = s.substring(sep + 3)
            assumed = false
        } else {
            scheme = "https"
            rest = s
            assumed = true
        }
        val authorityAndPath = rest.substringBefore('?').substringBefore('#').trimEnd('/')
        if (authorityAndPath.isEmpty() || authorityAndPath.any { it.isWhitespace() }) {
            return UrlParseResult.Invalid(UrlProblem.InvalidHost)
        }
        val canonical = "$scheme://$authorityAndPath"
        val uri = try {
            URI(canonical)
        } catch (e: URISyntaxException) {
            return UrlParseResult.Invalid(UrlProblem.InvalidHost)
        }
        val validPort = uri.port == -1 || uri.port in 1..65535
        if (uri.host.isNullOrBlank() || uri.userInfo != null || !validPort) {
            return UrlParseResult.Invalid(UrlProblem.InvalidHost)
        }
        return UrlParseResult.Valid(canonical, assumed)
    }

    /** Host (plus port if any) for display; falls back to the input. */
    fun displayHost(url: String): String = try {
        val uri = URI(url)
        if (uri.host == null) url else if (uri.port != -1) "${uri.host}:${uri.port}" else uri.host
    } catch (e: URISyntaxException) {
        url
    }
}
