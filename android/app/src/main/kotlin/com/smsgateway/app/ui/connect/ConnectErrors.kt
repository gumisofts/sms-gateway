package com.smsgateway.app.ui.connect

import com.smsgateway.app.gateway.ApiFailure

data class ErrorMessage(val title: String, val hint: String)

/** Maps a typed [ApiFailure] to text for the user. */
fun ApiFailure.toMessage(): ErrorMessage = when (this) {
    ApiFailure.InvalidUrl -> ErrorMessage("That is not a valid server URL", "Use a form like https://gateway.example.com.")
    ApiFailure.UnknownHost -> ErrorMessage(
        "Server not found",
        "The host name could not be resolved. Check the spelling and that this phone has internet access.",
    )
    ApiFailure.Timeout -> ErrorMessage(
        "Server did not respond in time",
        "Check that the address and port are right, the server is running and no firewall is blocking it.",
    )
    ApiFailure.ConnectionRefused -> ErrorMessage(
        "Connection refused",
        "The host is reachable but nothing is listening on that port. Check the port number and that the server is running.",
    )
    ApiFailure.Unreachable -> ErrorMessage(
        "Could not reach the server",
        "Check your network connection. On a local network, the phone must be able to reach the server's LAN IP.",
    )
    ApiFailure.CleartextBlocked -> ErrorMessage(
        "Plain http:// is blocked",
        "Release builds only allow https://. Use an https:// address (only debug builds may use http://).",
    )
    ApiFailure.TlsError -> ErrorMessage(
        "Secure connection failed",
        "The server's TLS certificate is invalid, expired or not trusted by this phone, or the server does not speak https on that port.",
    )
    ApiFailure.KeyRejected -> ErrorMessage(
        "Registration key rejected",
        "The server refused this key. Check it with the server administrator.",
    )
    ApiFailure.NotFound -> ErrorMessage(
        "This does not look like an SMS gateway server",
        "The server answered 404 for /api/nodes/register. Check the URL (including any path prefix).",
    )
    is ApiFailure.ServerError -> ErrorMessage(
        "The server had an error (HTTP $code)",
        "The address is right but the server failed. Try again, or check the server logs.",
    )
    is ApiFailure.UnexpectedStatus -> ErrorMessage(
        "Unexpected response from server (HTTP $code)",
        "Check that the URL points at the SMS gateway backend.",
    )
    ApiFailure.MalformedResponse -> ErrorMessage(
        "Unexpected reply from server",
        "The server answered but not in the expected format. Check that the URL points at the SMS gateway backend.",
    )
}
