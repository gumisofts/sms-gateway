import asyncio
from datetime import timedelta

from asgiref.sync import sync_to_async
from channels.layers import channel_layers
from channels.testing import WebsocketCommunicator
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.asgi import app

from . import services
from .factories import REGISTER_PAYLOAD, make_node, make_user, register
from .models import Node, NodeToken, hash_token


@override_settings(NODE_REGISTRATION_KEY="s3cret")
class RegistrationTests(TestCase):
    url = "/api/nodes/register"

    def post(self, **extra):
        return APIClient().post(self.url, {**REGISTER_PAYLOAD, **extra}, format="json")

    def test_requires_registration_key(self):
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(self.post(registrationKey="nope").status_code, 403)
        self.assertEqual(Node.objects.count(), 0)

    def test_registers_and_stores_only_a_hash(self):
        response = self.post(registrationKey="s3cret")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        node = Node.objects.get(node_id=body["nodeId"])
        self.assertEqual(node.sims.get().subscription_id, 3)
        self.assertEqual(node.device_name, "Pixel 7")
        stored = NodeToken.objects.get(node=node).token_hash
        self.assertNotEqual(stored, body["token"])
        self.assertEqual(stored, hash_token(body["token"]))
        self.assertEqual(NodeToken.authenticate(body["token"]), node)

    def test_previous_node_id_reuses_node_and_rotates_token(self):
        first = self.post(registrationKey="s3cret").json()
        second = self.post(
            registrationKey="s3cret",
            previousNodeId=first["nodeId"],
            sims=[{"slot": 1, "subscriptionId": 9, "carrier": "Other"}],
        ).json()
        self.assertEqual(second["nodeId"], first["nodeId"])
        self.assertNotEqual(second["token"], first["token"])
        self.assertEqual(Node.objects.count(), 1)
        self.assertIsNone(NodeToken.authenticate(first["token"]))
        self.assertIsNotNone(NodeToken.authenticate(second["token"]))
        self.assertEqual([s.slot for s in Node.objects.get().sims.all()], [1])

    def test_unknown_previous_node_id_creates_a_new_node(self):
        body = self.post(registrationKey="s3cret", previousNodeId="ghost").json()
        self.assertNotEqual(body["nodeId"], "ghost")

    def test_disabled_node_cannot_reregister(self):
        body = self.post(registrationKey="s3cret").json()
        Node.objects.update(is_active=False)
        response = self.post(registrationKey="s3cret", previousNodeId=body["nodeId"])
        self.assertEqual(response.status_code, 403)

    def test_invalid_payload(self):
        response = APIClient().post(
            self.url, {"registrationKey": "s3cret"}, format="json"
        )
        self.assertEqual(response.status_code, 400)

    @override_settings(NODE_REGISTRATION_KEY="", DEBUG=False)
    def test_no_key_configured_is_closed_outside_debug(self):
        self.assertEqual(self.post().status_code, 403)

    @override_settings(NODE_REGISTRATION_KEY="", DEBUG=True)
    def test_no_key_configured_is_open_in_debug(self):
        self.assertEqual(self.post().status_code, 200)


class NodeAdminApiTests(TestCase):
    def test_requires_staff(self):
        node = make_node()
        client = APIClient()
        self.assertIn(client.get("/api/nodes/").status_code, (401, 403))
        client.force_authenticate(make_user())
        self.assertEqual(client.get("/api/nodes/").status_code, 403)
        self.assertEqual(
            client.post(f"/api/nodes/{node.node_id}/disable/").status_code, 403
        )

    def test_list_retrieve_disable_enable_and_config(self):
        node = make_node(battery_percent=80, queue_depth=2)
        client = APIClient()
        client.force_authenticate(make_user(staff=True))

        listing = client.get("/api/nodes/").json()
        self.assertEqual(len(listing), 1)
        detail = client.get(f"/api/nodes/{node.node_id}/").json()
        self.assertTrue(detail["online"])
        self.assertEqual(detail["sims"][0]["subscription_id"], 3)
        self.assertEqual(detail["stats"]["battery_percent"], 80)

        self.assertFalse(
            client.post(f"/api/nodes/{node.node_id}/disable/").json()["is_active"]
        )
        node.refresh_from_db()
        self.assertFalse(node.connected)
        self.assertTrue(
            client.post(f"/api/nodes/{node.node_id}/enable/").json()["is_active"]
        )

        response = client.post(
            f"/api/nodes/{node.node_id}/config/",
            {"rate_per_minute": 30, "heartbeat_interval_sec": 20},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        node.refresh_from_db()
        self.assertEqual(node.desired_rate_per_minute, 30)
        bad = client.post(
            f"/api/nodes/{node.node_id}/config/",
            {"heartbeat_interval_sec": 1},
            format="json",
        )
        self.assertEqual(bad.status_code, 400)

    def test_unknown_node_is_404(self):
        client = APIClient()
        client.force_authenticate(make_user(staff=True))
        self.assertEqual(client.post("/api/nodes/nope/disable/").status_code, 404)


class StaleSweepTests(TestCase):
    def test_marks_silent_nodes_offline(self):
        old = timezone.now() - timedelta(minutes=10)
        stale = make_node(last_seen_at=old)
        fresh = make_node(last_seen_at=timezone.now())
        self.assertEqual(services.mark_stale_offline(), 1)
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertFalse(stale.connected)
        self.assertTrue(fresh.connected)


class WebSocketCase(TransactionTestCase):
    """Base for tests that drive the real ASGI app through the in-memory layer."""

    def setUp(self):
        # InMemoryChannelLayer queues are bound to an event loop: start clean.
        channel_layers.backends = {}
        self.communicators = []

    def open(self, node_id, token=None):
        headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
        comm = WebsocketCommunicator(app, f"/ws/nodes/{node_id}", headers=headers)
        self.communicators.append(comm)
        return comm

    async def connect_node(self, node_id, token):
        comm = self.open(node_id, token)
        connected, _ = await comm.connect()
        self.assertTrue(connected)
        config = await comm.receive_json_from(timeout=2)
        self.assertEqual(config["type"], "config")
        return comm

    async def eventually(self, check, timeout=3.0):
        """Poll a sync callable (run in a DB thread) until it returns truthy."""
        end = asyncio.get_running_loop().time() + timeout
        while True:
            value = await sync_to_async(check)()
            if value:
                return value
            if asyncio.get_running_loop().time() > end:
                self.fail("condition not reached in time")
            await asyncio.sleep(0.02)

    async def asyncTearDown(self):
        for comm in self.communicators:
            try:
                await comm.disconnect()
            except Exception:  # already closed
                pass


class WebSocketAuthTests(WebSocketCase):
    async def test_rejects_missing_wrong_and_mismatched_credentials(self):
        node, token = await sync_to_async(register)()
        other, other_token = await sync_to_async(register)()

        for node_id, tok in [
            (node.node_id, None),
            (node.node_id, "garbage"),
            (node.node_id, other_token),  # valid token, other node
            ("missing-node", token),
        ]:
            comm = self.open(node_id, tok)
            connected, _ = await comm.connect()
            self.assertFalse(connected, (node_id, tok))

    async def test_origin_header_is_not_required(self):
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        await comm.disconnect()

    async def test_disabled_node_is_rejected(self):
        node, token = await sync_to_async(register)()
        await sync_to_async(Node.objects.filter(pk=node.pk).update)(is_active=False)
        connected, _ = await self.open(node.node_id, token).connect()
        self.assertFalse(connected)

    async def test_connect_marks_online_and_disconnect_offline(self):
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        row = await Node.objects.aget(pk=node.pk)
        self.assertTrue(row.connected)
        self.assertTrue(row.connection_id)
        await comm.disconnect()
        await self.eventually(lambda: not Node.objects.get(pk=node.pk).connected)

    async def test_newest_connection_wins(self):
        node, token = await sync_to_async(register)()
        first = await self.connect_node(node.node_id, token)
        second = await self.connect_node(node.node_id, token)
        message = await first.receive_output(timeout=2)
        self.assertEqual(message["type"], "websocket.close")
        # The old connection closing must not flip the node offline.
        await first.wait()
        await asyncio.sleep(0.1)
        self.assertTrue((await Node.objects.aget(pk=node.pk)).connected)
        await second.disconnect()

    async def test_heartbeat_updates_stats_and_sims(self):
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        await comm.send_json_to(
            {
                "type": "heartbeat",
                "batteryPercent": 81,
                "charging": True,
                "signalLevel": 3,
                "sims": [{"slot": 1, "subscriptionId": 7, "carrier": "Orange"}],
                "queueDepth": 4,
                "ratePerMinute": 20,
                "timestamp": 1760000000000,
            }
        )
        row = await self.eventually(
            lambda: Node.objects.filter(pk=node.pk, queue_depth=4).first()
        )
        self.assertEqual(
            (row.battery_percent, row.charging, row.signal_level), (81, True, 3)
        )
        self.assertEqual(row.rate_per_minute, 20)
        sims = await sync_to_async(
            lambda: [(s.slot, s.subscription_id) for s in row.sims.all()]
        )()
        self.assertEqual(sims, [(1, 7)])

    async def test_unknown_and_malformed_frames_are_ignored(self):
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        await comm.send_json_to({"type": "future_thing", "x": 1})
        await comm.send_json_to({"type": "ack"})  # missing fields
        await comm.send_json_to([1, 2])
        await comm.send_json_to(
            {"type": "heartbeat", "queueDepth": 1, "batteryPercent": 5}
        )
        await self.eventually(lambda: Node.objects.get(pk=node.pk).queue_depth == 1)

    async def test_config_is_sent_on_connect_and_pushed(self):
        node, token = await sync_to_async(register)()
        await sync_to_async(Node.objects.filter(pk=node.pk).update)(
            desired_rate_per_minute=15
        )
        comm = self.open(node.node_id, token)
        await comm.connect()
        config = await comm.receive_json_from(timeout=2)
        self.assertEqual(config["ratePerMinute"], 15)
        self.assertIn("heartbeatIntervalSec", config)

        staff = await sync_to_async(make_user)(staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        await sync_to_async(client.post)(
            f"/api/nodes/{node.node_id}/config/",
            {"heartbeat_interval_sec": 10},
            format="json",
        )
        pushed = await comm.receive_json_from(timeout=2)
        self.assertEqual(pushed, {"type": "config", "heartbeatIntervalSec": 10})

    async def test_disable_kicks_the_node(self):
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        staff = await sync_to_async(make_user)(staff=True)
        client = APIClient()
        client.force_authenticate(staff)
        await sync_to_async(client.post)(f"/api/nodes/{node.node_id}/disable/")
        message = await comm.receive_output(timeout=2)
        self.assertEqual(message["type"], "websocket.close")
        connected, _ = await self.open(node.node_id, token).connect()
        self.assertFalse(connected)
