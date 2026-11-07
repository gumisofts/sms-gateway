package com.smsgateway.app

import com.smsgateway.app.data.Settings
import com.smsgateway.app.gateway.ApiFailure
import com.smsgateway.app.gateway.ConnectionState
import com.smsgateway.app.gateway.GatewayStatus
import com.smsgateway.app.gateway.ServerUrl
import com.smsgateway.app.gateway.UrlParseResult
import com.smsgateway.app.gateway.UrlProblem
import com.smsgateway.app.service.NotificationText
import com.smsgateway.app.ui.connect.Route
import com.smsgateway.app.ui.connect.chooseRoute
import com.smsgateway.app.ui.connect.toMessage
import com.smsgateway.app.ui.home.StatusAction
import com.smsgateway.app.ui.home.StatusTone
import com.smsgateway.app.ui.home.countdownSeconds
import com.smsgateway.app.ui.home.formatCountdown
import com.smsgateway.app.ui.home.presentStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.UnknownServiceException
import javax.net.ssl.SSLHandshakeException

class ConnectLogicTest {
    private fun valid(input: String) = ServerUrl.parse(input) as UrlParseResult.Valid
    private fun problem(input: String) = (ServerUrl.parse(input) as UrlParseResult.Invalid).problem

    @Test
    fun urlAssumesHttpsWhenSchemeMissing() {
        val v = valid("gateway.example.com")
        assertEquals("https://gateway.example.com", v.url)
        assertTrue(v.schemeAssumed)
    }

    @Test
    fun urlKeepsExplicitScheme() {
        val v = valid("  http://192.168.1.10:8000/ ")
        assertEquals("http://192.168.1.10:8000", v.url)
        assertFalse(v.schemeAssumed)
        assertEquals("https://x.io/api", valid("HTTPS://x.io/api//").url)
    }

    @Test
    fun urlDropsQueryAndFragmentAndTrailingSlash() {
        assertEquals("https://x.io", valid("https://x.io/?a=1#f").url)
        assertEquals("https://localhost:8000", valid("localhost:8000").url)
    }

    @Test
    fun urlRejectsInvalidInput() {
        assertEquals(UrlProblem.Empty, problem("   "))
        assertEquals(UrlProblem.UnsupportedScheme, problem("ftp://x.io"))
        assertEquals(UrlProblem.UnsupportedScheme, problem("://x.io"))
        assertEquals(UrlProblem.InvalidHost, problem("https://"))
        assertEquals(UrlProblem.InvalidHost, problem("http://exa mple.com"))
        assertEquals(UrlProblem.InvalidHost, problem("host:abc"))
        assertEquals(UrlProblem.InvalidHost, problem("https://x.io:99999"))
        assertEquals(UrlProblem.InvalidHost, problem("https://user:pw@x.io"))
    }

    @Test
    fun displayHost() {
        assertEquals("gw.example.com", ServerUrl.displayHost("https://gw.example.com/base"))
        assertEquals("10.0.0.5:8000", ServerUrl.displayHost("http://10.0.0.5:8000"))
    }

    @Test
    fun throwableClassification() {
        assertEquals(ApiFailure.UnknownHost, ApiFailure.fromThrowable(UnknownHostException("x")))
        assertEquals(ApiFailure.Timeout, ApiFailure.fromThrowable(SocketTimeoutException("t")))
        assertEquals(ApiFailure.ConnectionRefused, ApiFailure.fromThrowable(ConnectException("Connection refused")))
        assertEquals(ApiFailure.ConnectionRefused, ApiFailure.fromThrowable(IOException("Failed", ConnectException("ECONNREFUSED (Connection refused)"))))
        assertEquals(ApiFailure.Unreachable, ApiFailure.fromThrowable(ConnectException("Network is unreachable")))
        assertEquals(ApiFailure.TlsError, ApiFailure.fromThrowable(SSLHandshakeException("bad cert")))
        assertEquals(ApiFailure.CleartextBlocked, ApiFailure.fromThrowable(UnknownServiceException("CLEARTEXT communication to x not permitted by network security policy")))
        assertEquals(ApiFailure.CleartextBlocked, ApiFailure.fromThrowable(IOException("Cleartext HTTP traffic to x not permitted")))
        assertEquals(ApiFailure.Unreachable, ApiFailure.fromThrowable(IOException("boom")))
    }

    @Test
    fun httpStatusClassification() {
        assertEquals(ApiFailure.KeyRejected, ApiFailure.fromHttpStatus(403))
        assertEquals(ApiFailure.KeyRejected, ApiFailure.fromHttpStatus(401))
        assertEquals(ApiFailure.NotFound, ApiFailure.fromHttpStatus(404))
        assertEquals(ApiFailure.ServerError(502), ApiFailure.fromHttpStatus(502))
        assertEquals(ApiFailure.UnexpectedStatus(400), ApiFailure.fromHttpStatus(400))
    }

    @Test
    fun everyFailureHasADistinctMessage() {
        val all = listOf(
            ApiFailure.InvalidUrl, ApiFailure.UnknownHost, ApiFailure.Timeout, ApiFailure.ConnectionRefused,
            ApiFailure.Unreachable, ApiFailure.CleartextBlocked, ApiFailure.TlsError, ApiFailure.KeyRejected,
            ApiFailure.NotFound, ApiFailure.ServerError(500), ApiFailure.UnexpectedStatus(418), ApiFailure.MalformedResponse,
        )
        val titles = all.map { it.toMessage().title }
        assertEquals(titles.size, titles.toSet().size)
        assertTrue(all.all { it.toMessage().hint.isNotBlank() })
        assertTrue(ApiFailure.ServerError(503).toMessage().title.contains("503"))
    }

    @Test
    fun routing() {
        val registered = Settings(backendUrl = "https://x.io", nodeId = "n", token = "t")
        assertEquals(Route.Connect, chooseRoute(Settings(), connecting = false))
        assertEquals(Route.Home, chooseRoute(registered, connecting = false))
        assertEquals(Route.Connect, chooseRoute(registered, connecting = true))
        assertEquals(Route.Home, chooseRoute(Settings(localMode = true), connecting = false))
        assertEquals(Route.Connect, chooseRoute(Settings(backendUrl = "https://x.io"), connecting = false))
    }

    @Test
    fun statusReducerTracksTimestamps() {
        var s = GatewayStatus()
        s = s.next(ConnectionState.Connecting, 100)
        s = s.next(ConnectionState.Connected, 200)
        assertEquals(200L, s.connectedSince)
        s = s.next(ConnectionState.Connected, 250) // same state again keeps the original time
        assertEquals(200L, s.connectedSince)
        s = s.next(ConnectionState.Disconnected("closed", 5_000), 300)
        assertNull(s.connectedSince)
        assertEquals(300L, s.lastConnectedAt)
        assertEquals("closed", s.lastError)
        assertEquals(5_300L, s.retryAt)
        s = s.next(ConnectionState.Connecting, 400)
        assertNull(s.retryAt)
        assertEquals(300L, s.lastConnectedAt)
        s = s.next(ConnectionState.Connected, 500)
        assertNull(s.lastError)
    }

    @Test
    fun statusPresentation() {
        val creds = true
        val disc = GatewayStatus().next(ConnectionState.Disconnected(null, 10_000), 1_000)
        val p1 = presentStatus(disc, creds, now = 1_000)
        assertEquals(StatusTone.Error, p1.tone)
        assertEquals("Disconnected – retrying in 10s", p1.label)
        assertTrue(p1.ticking)
        assertEquals("Disconnected – retrying in 5s", presentStatus(disc, creds, now = 6_500).label)
        assertEquals("Disconnected – retrying...", presentStatus(disc, creds, now = 12_000).label)

        val conn = GatewayStatus().next(ConnectionState.Connected, 0)
        assertEquals(StatusTone.Connected, presentStatus(conn, creds, 5).tone)
        assertEquals("Connected", presentStatus(conn, creds, 5).label)

        val connecting = GatewayStatus().next(ConnectionState.Connecting, 0)
        assertTrue(presentStatus(connecting, creds, 5).animated)
        assertEquals(StatusTone.Connecting, presentStatus(connecting, creds, 5).tone)

        val unauth = GatewayStatus().next(ConnectionState.Unauthorized(30_000), 0)
        assertEquals(StatusAction.ReRegister, presentStatus(unauth, creds, 5).action)
        assertEquals(StatusTone.Error, presentStatus(unauth, creds, 5).tone)

        val off = presentStatus(GatewayStatus(), creds, 5)
        assertEquals(StatusTone.Off, off.tone)
        assertEquals(StatusAction.TurnOn, off.action)
        assertEquals("Gateway off", off.label)

        assertEquals("No server configured", presentStatus(conn, hasCredentials = false, now = 5).label)
    }

    @Test
    fun countdownFormatting() {
        assertEquals(0L, countdownSeconds(1_000, 2_000))
        assertEquals(1L, countdownSeconds(1_001, 1_000))
        assertEquals("59s", formatCountdown(59))
        assertEquals("1m 05s", formatCountdown(65))
    }

    @Test
    fun notificationText() {
        assertEquals("Connected - queue: 2", NotificationText.describe(ConnectionState.Connected, 2))
        assertEquals("Reconnecting in 5s - queue: 0", NotificationText.describe(ConnectionState.Disconnected(null, 4_200), 0))
        assertTrue(NotificationText.connection(ConnectionState.Unauthorized(1)).startsWith("Unauthorized"))
        assertEquals("Off", NotificationText.connection(ConnectionState.Stopped))
    }
}
