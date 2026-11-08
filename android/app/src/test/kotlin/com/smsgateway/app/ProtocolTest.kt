package com.smsgateway.app

import com.smsgateway.app.gateway.GatewayEndpoints
import com.smsgateway.app.gateway.GatewayJson
import com.smsgateway.app.gateway.NodeMessage
import com.smsgateway.app.gateway.RegisterRequest
import com.smsgateway.app.gateway.ServerMessage
import com.smsgateway.app.gateway.SmsLimitInfo
import com.smsgateway.app.sms.SimInfo
import kotlinx.serialization.SerializationException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ProtocolTest {
    private val json = GatewayJson.instance

    @Test
    fun serverMessagesRoundTrip() {
        val msgs = listOf<ServerMessage>(
            ServerMessage.SendSms("j1", "+15551234567", "hello", simSlot = 1),
            ServerMessage.Config(ratePerMinute = 10),
        )
        for (m in msgs) {
            val text = json.encodeToString(ServerMessage.serializer(), m)
            assertEquals(m, json.decodeFromString(ServerMessage.serializer(), text))
        }
    }

    @Test
    fun nodeMessagesRoundTripAndCarryTypeDiscriminator() {
        val msgs = listOf<NodeMessage>(
            NodeMessage.Ack("j1", true),
            NodeMessage.Ack("j2", false, "queue_full"),
            NodeMessage.StatusUpdate("j1", "failed", 3, 4, "NO_SERVICE", 123L),
            NodeMessage.Heartbeat(80, true, 3, listOf(SimInfo(0, 5, "Carrier")), 2, 20, 99L),
            NodeMessage.StatusUpdate("j1", "sent", 1, timestamp = 5L, parts = 3),
            NodeMessage.Heartbeat(80, true, null, emptyList(), 0, 20, 99L, SmsLimitInfo(true, 100000, 60000L)),
        )
        for (m in msgs) {
            val text = json.encodeToString(NodeMessage.serializer(), m)
            assertEquals(m, json.decodeFromString(NodeMessage.serializer(), text))
        }
        val ack = json.encodeToString(NodeMessage.serializer(), NodeMessage.Ack("j1", true))
        assertTrue(ack, ack.contains("\"type\":\"ack\""))
        assertTrue("nulls omitted: $ack", !ack.contains("reason"))
    }

    @Test
    fun partsAndSmsLimitAreOptionalOnTheWire() {
        val su = json.encodeToString(NodeMessage.serializer(), NodeMessage.StatusUpdate("j", "sent", 1, timestamp = 1L))
        assertTrue(su, !su.contains("parts"))
        val su2 = json.encodeToString(NodeMessage.serializer(), NodeMessage.StatusUpdate("j", "sent", 1, timestamp = 1L, parts = 2))
        assertTrue(su2, su2.contains("\"parts\":2"))
        val hb = json.encodeToString(
            NodeMessage.serializer(),
            NodeMessage.Heartbeat(1, false, null, emptyList(), 0, 20, 1L, SmsLimitInfo(raised = true)),
        )
        assertTrue(hb, hb.contains("\"smsLimit\":{\"raised\":true}"))
    }

    @Test
    fun decodesDocumentedSendSmsAndIgnoresUnknownFields() {
        val m = json.decodeFromString(
            ServerMessage.serializer(),
            """{"type":"send_sms","id":"a","to":"+1","body":"x","future":true}""",
        )
        assertEquals(ServerMessage.SendSms("a", "+1", "x"), m)
        assertNull((m as ServerMessage.SendSms).subscriptionId)
    }

    @Test
    fun unknownTypeThrowsSerializationException() {
        try {
            json.decodeFromString(ServerMessage.serializer(), """{"type":"nope"}""")
            fail("expected exception")
        } catch (_: SerializationException) {
        }
    }

    @Test
    fun registerRequestOmitsNullOptionals() {
        val text = json.encodeToString(RegisterRequest.serializer(), RegisterRequest("d", "m", "mo", "14", 34, "0.1", emptyList()))
        assertTrue(text, !text.contains("registrationKey") && !text.contains("previousNodeId"))
    }

    @Test
    fun endpoints() {
        assertEquals("https://x.io/api/nodes/register", GatewayEndpoints.register("https://x.io/"))
        assertEquals("wss://x.io/ws/nodes/n1", GatewayEndpoints.websocket("https://x.io/", "n1"))
        assertEquals("ws://10.0.2.2:8080/ws/nodes/a%20b", GatewayEndpoints.websocket("http://10.0.2.2:8080", "a b"))
        assertEquals("https://x.io", GatewayEndpoints.normalizeBaseUrl("  https://x.io/ "))
        assertNull(GatewayEndpoints.normalizeBaseUrl("x.io"))
        assertNull(GatewayEndpoints.normalizeBaseUrl("http://"))
    }
}
