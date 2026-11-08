import asyncio
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from nodes.factories import make_node, make_user, register
from nodes.models import Node
from nodes.tests import WebSocketCase

from . import dispatch, services
from .models import ApiKey, SmsMessage, Status

MSG = {"to": "+15551234567", "body": "Hello"}


def make_message(owner=None, **fields):
    owner = owner or make_user()
    return SmsMessage.objects.create(
        owner=owner, to=MSG["to"], body=MSG["body"], **fields
    )


def api_client_for(user):
    _, key = ApiKey.create_for(user, "test")
    client = APIClient()
    client.credentials(HTTP_X_API_KEY=key)
    return client


# --------------------------------------------------------------------- model


class StatusRuleTests(TestCase):
    def setUp(self):
        self.node = make_node()
        self.message = make_message(node=self.node, status=Status.DISPATCHED)

    def apply(self, status, attempts=1, **kw):
        return services.apply_node_status(
            self.node,
            {"id": str(self.message.id), "status": status, "attempts": attempts, **kw},
        )

    def current(self):
        self.message.refresh_from_db()
        return self.message

    def test_happy_path_sets_timestamps(self):
        for status in ("queued", "sending", "sent", "delivered"):
            self.assertTrue(self.apply(status), status)
        m = self.current()
        self.assertEqual(m.status, Status.DELIVERED)
        self.assertIsNotNone(m.sent_at)
        self.assertIsNotNone(m.delivered_at)
        self.assertIsNotNone(m.acked_at)

    def test_duplicate_update_is_a_noop(self):
        self.assertTrue(self.apply("sent"))
        before = self.current().updated_at
        self.assertFalse(self.apply("sent"))
        self.assertEqual(self.current().updated_at, before)

    def test_intermediate_states_may_be_skipped(self):
        self.assertTrue(self.apply("queued", attempts=0))
        self.assertTrue(self.apply("sent"))
        self.assertEqual(self.current().status, Status.SENT)

    def test_terminal_states_never_move(self):
        self.assertTrue(self.apply("delivered"))
        for status in ("sent", "sending", "queued", "failed"):
            self.assertFalse(self.apply(status, attempts=9), status)
        self.assertEqual(self.current().status, Status.DELIVERED)

        other = make_message(node=self.node, status=Status.DISPATCHED)
        data = {"id": str(other.id), "attempts": 5}
        services.apply_node_status(
            self.node,
            {**data, "status": "failed", "errorCode": 4, "errorMessage": "NO_SERVICE"},
        )
        self.assertFalse(
            services.apply_node_status(self.node, {**data, "status": "delivered"})
        )
        other.refresh_from_db()
        self.assertEqual((other.status, other.error_code), (Status.FAILED, 4))
        self.assertEqual(other.error_message, "NO_SERVICE")

    def test_cancelled_message_ignores_node_updates(self):
        services.cancel_message(self.message.id)
        self.assertFalse(self.apply("sent"))
        self.assertEqual(self.current().status, Status.CANCELLED)

    def test_does_not_go_back_from_sent_and_retry_is_tracked(self):
        self.assertTrue(self.apply("sending", attempts=1))
        self.assertTrue(self.apply("queued", attempts=1, errorCode=4))  # retry
        self.assertTrue(self.apply("sending", attempts=2))
        self.assertFalse(self.apply("queued", attempts=1))  # stale replay
        self.assertTrue(self.apply("sent", attempts=2))
        self.assertFalse(self.apply("sending", attempts=3))
        self.assertEqual(self.current().status, Status.SENT)
        self.assertEqual(self.current().attempts, 2)

    def test_update_from_another_node_or_unknown_id_is_ignored(self):
        other = make_node()
        self.assertFalse(
            services.apply_node_status(
                other, {"id": str(self.message.id), "status": "sent", "attempts": 1}
            )
        )
        self.assertFalse(
            services.apply_node_status(
                self.node, {"id": "nope", "status": "sent", "attempts": 1}
            )
        )
        self.assertFalse(self.apply("bogus"))

    def test_future_node_timestamp_is_clamped(self):
        self.apply(
            "sent",
            timestamp=int((timezone.now() + timedelta(days=1)).timestamp() * 1000),
        )
        self.assertLessEqual(self.current().sent_at, timezone.now())


# ---------------------------------------------------------------- dispatcher


class DispatcherTests(TestCase):
    def dispatch(self, message, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            result = dispatch.dispatch_message(message.id, **kw)
        message.refresh_from_db()
        return result

    def test_no_online_node_leaves_message_pending(self):
        make_node(connected=False)
        make_node(is_active=False)
        message = make_message()
        self.assertFalse(self.dispatch(message))
        self.assertEqual(message.status, Status.PENDING)

    def test_least_loaded_node_wins(self):
        busy = make_node(queue_depth=5)
        idle = make_node(queue_depth=1)
        message = make_message()
        self.assertTrue(self.dispatch(message))
        self.assertEqual(message.node, idle)
        self.assertEqual(message.status, Status.DISPATCHED)
        self.assertEqual(message.dispatch_attempts, 1)
        self.assertIsNotNone(message.dispatched_at)
        self.assertNotEqual(message.node, busy)

    def test_unacked_in_flight_counts_towards_load(self):
        a = make_node()
        b = make_node()
        owner = make_user()
        for _ in range(3):  # three unacked jobs on a
            make_message(owner, node=a, status=Status.DISPATCHED)
        message = make_message(owner)
        self.dispatch(message)
        self.assertEqual(message.node, b)

    def test_spreads_a_batch_across_nodes(self):
        a, b = make_node(), make_node()
        owner = make_user()
        messages = [make_message(owner) for _ in range(6)]
        for m in messages:
            self.dispatch(m)
        counts = {a.pk: 0, b.pk: 0}
        for m in messages:
            counts[m.node_id] += 1
        self.assertEqual(counts, {a.pk: 3, b.pk: 3})

    def test_simless_node_at_its_rate_is_skipped(self):
        # Nodes without SIM info are one lane paced by the node's own rate.
        limited = make_node(rate_per_minute=2, sims=())
        other = make_node(queue_depth=50, sims=())
        owner = make_user()
        for _ in range(2):
            make_message(
                owner, node=limited, status=Status.QUEUED, dispatched_at=timezone.now()
            )
        message = make_message(owner)
        self.dispatch(message)
        self.assertEqual(message.node, other)
        other.delete()
        again = make_message(owner)
        self.assertFalse(self.dispatch(again))

    def test_requested_node_and_sim_are_honoured(self):
        a = make_node(sims=((0, 3),))
        b = make_node(sims=((0, 4), (1, 5)))
        owner = make_user()

        m = make_message(owner, requested_node=b)
        self.dispatch(m)
        self.assertEqual(m.node, b)

        m = make_message(owner, subscription_id=3)
        self.dispatch(m)
        self.assertEqual(m.node, a)

        m = make_message(owner, sim_slot=1)
        self.dispatch(m)
        self.assertEqual(m.node, b)

        m = make_message(owner, sim_slot=7)  # nobody has it
        self.assertFalse(self.dispatch(m))

        b.connected = False
        b.save()
        m = make_message(owner, requested_node=b)  # requested node offline: wait
        self.assertFalse(self.dispatch(m))

    def test_private_nodes_only_serve_their_owner(self):
        owner, stranger = make_user(), make_user()
        make_node(owner=owner)
        self.assertFalse(self.dispatch(make_message(stranger)))
        self.assertTrue(self.dispatch(make_message(owner)))

    def test_already_dispatched_message_is_not_dispatched_twice(self):
        make_node()
        message = make_message()
        self.assertTrue(self.dispatch(message))
        self.assertFalse(self.dispatch(message))
        self.assertEqual(message.dispatch_attempts, 1)

    def test_rejection_excludes_that_node_next_time(self):
        a, b = make_node(), make_node(queue_depth=9)
        message = make_message()
        self.dispatch(message)
        self.assertEqual(message.node, a)
        self.assertTrue(services.handle_ack(a, message.id, False, "queue_full"))
        self.dispatch(message)
        self.assertEqual(message.node, b)

    def test_invalid_job_ack_fails_the_message(self):
        a = make_node()
        message = make_message()
        self.dispatch(message)
        self.assertFalse(services.handle_ack(a, message.id, False, "invalid_job"))
        message.refresh_from_db()
        self.assertEqual(message.status, Status.FAILED)

    def test_gives_up_after_max_dispatch_attempts(self):
        make_node()
        message = make_message(dispatch_attempts=5)
        self.assertFalse(self.dispatch(message))
        self.assertEqual(message.status, Status.FAILED)

    def test_pending_sweep_retries_tried_nodes(self):
        only = make_node()
        make_message(tried_nodes=[only.node_id])
        self.assertEqual(dispatch.dispatch_pending(), 0)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(dispatch.dispatch_pending(allow_tried=True), 1)


class SweeperTests(TestCase):
    @override_settings(MESSAGE_ACK_TIMEOUT_SEC=60)
    def test_unacked_on_offline_node_moves_to_another_node(self):
        dead = make_node(connected=False)
        alive = make_node()
        old = timezone.now() - timedelta(minutes=5)
        message = make_message(
            node=dead, status=Status.DISPATCHED, dispatched_at=old, dispatch_attempts=1
        )
        fresh = make_message(
            node=dead, status=Status.DISPATCHED, dispatched_at=timezone.now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(dispatch.redispatch_unacked(), 1)
        message.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual((message.status, message.node), (Status.DISPATCHED, alive))
        self.assertEqual(message.dispatch_attempts, 2)
        self.assertEqual(fresh.node, dead)

    @override_settings(MESSAGE_ACK_TIMEOUT_SEC=60)
    def test_unacked_on_connected_node_is_resent_to_same_node(self):
        node = make_node()
        old = timezone.now() - timedelta(minutes=5)
        message = make_message(
            node=node, status=Status.DISPATCHED, dispatched_at=old, dispatch_attempts=1
        )
        with self.captureOnCommitCallbacks(execute=True):
            dispatch.redispatch_unacked()
        message.refresh_from_db()
        self.assertEqual(message.node, node)
        self.assertGreater(message.dispatched_at, old)

    @override_settings(MESSAGE_PENDING_TTL_SEC=60)
    def test_expires_old_pending_messages_only(self):
        old = make_message()
        SmsMessage.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        young = make_message()
        acked = make_message(status=Status.QUEUED)
        SmsMessage.objects.filter(pk=acked.pk).update(
            created_at=timezone.now() - timedelta(hours=1)
        )
        self.assertEqual(services.expire_pending(), 1)
        for m in (old, young, acked):
            m.refresh_from_db()
        self.assertEqual(
            (old.status, young.status, acked.status),
            (Status.EXPIRED, Status.PENDING, Status.QUEUED),
        )


# ----------------------------------------------------------------------- API


class ApiKeyTests(TestCase):
    def test_key_auth_and_storage(self):
        user = make_user()
        row, key = ApiKey.create_for(user, "ci")
        self.assertTrue(key.startswith("sgk_"))
        self.assertNotEqual(row.key_hash, key)
        self.assertTrue(key.startswith(row.prefix))

        client = APIClient()
        self.assertIn(client.get("/api/messages/").status_code, (401, 403))
        client.credentials(HTTP_X_API_KEY="sgk_wrong")
        self.assertIn(client.get("/api/messages/").status_code, (401, 403))
        client.credentials(HTTP_X_API_KEY=key)
        self.assertEqual(client.get("/api/messages/").status_code, 200)
        row.refresh_from_db()
        self.assertIsNotNone(row.last_used_at)

    def test_revoked_key_is_rejected(self):
        user = make_user()
        row, key = ApiKey.create_for(user, "ci")
        row.revoked_at = timezone.now()
        row.save()
        client = APIClient()
        client.credentials(HTTP_X_API_KEY=key)
        self.assertIn(client.get("/api/messages/").status_code, (401, 403))

    def test_manage_keys_with_jwt_style_auth(self):
        user = make_user()
        client = APIClient()
        client.force_authenticate(user)
        created = client.post("/api/api-keys/", {"name": "prod"}, format="json")
        self.assertEqual(created.status_code, 201)
        body = created.json()
        self.assertTrue(body["key"].startswith("sgk_"))

        listing = client.get("/api/api-keys/").json()
        self.assertEqual(len(listing), 1)
        self.assertNotIn("key", listing[0])

        self.assertEqual(client.delete(f"/api/api-keys/{body['id']}/").status_code, 204)
        self.assertIsNotNone(ApiKey.objects.get(pk=body["id"]).revoked_at)

        keyed = APIClient()
        keyed.credentials(HTTP_X_API_KEY=body["key"])
        self.assertIn(keyed.get("/api/messages/").status_code, (401, 403))

    def test_api_key_cannot_manage_keys(self):
        client = api_client_for(make_user())
        self.assertEqual(
            client.post("/api/api-keys/", {"name": "x"}, format="json").status_code, 403
        )
        self.assertEqual(client.get("/api/api-keys/").status_code, 403)

    def test_cannot_revoke_someone_elses_key(self):
        other_row, _ = ApiKey.create_for(make_user(), "theirs")
        client = APIClient()
        client.force_authenticate(make_user())
        self.assertEqual(
            client.delete(f"/api/api-keys/{other_row.pk}/").status_code, 404
        )


class MessageApiTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = api_client_for(self.user)

    def post(self, data=None, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post("/api/messages/", data or MSG, format="json", **kw)

    def test_create_validates_phone_numbers(self):
        for bad in ("12345", "+0123456789", "hello", "+1", "+1555123456789012345", ""):
            self.assertEqual(self.post({**MSG, "to": bad}).status_code, 400, bad)
        self.assertEqual(self.post({**MSG, "body": ""}).status_code, 400)
        ok = self.post({**MSG, "to": "+1 (555) 123-4567"})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(ok.json()["to"], "+15551234567")

    def test_create_returns_pending_when_no_node(self):
        body = self.post().json()
        self.assertEqual(body["status"], "pending")
        self.assertIsNone(body["node"])
        self.assertEqual(SmsMessage.objects.get(pk=body["id"]).owner, self.user)

    def test_create_dispatches_to_an_online_node(self):
        node = make_node()
        body = self.post().json()
        self.assertEqual(
            self.client.get(f"/api/messages/{body['id']}/").json()["status"],
            "dispatched",
        )
        self.assertEqual(SmsMessage.objects.get(pk=body["id"]).node, node)

    def test_client_reference_is_idempotent_per_owner(self):
        first = self.post({**MSG, "client_reference": "order-1"})
        second = self.post({**MSG, "client_reference": "order-1"})
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json()["id"], second.json()["id"])
        self.assertEqual(SmsMessage.objects.count(), 1)
        other = api_client_for(make_user()).post(
            "/api/messages/", {**MSG, "client_reference": "order-1"}, format="json"
        )
        self.assertEqual(other.status_code, 201)

    def test_unknown_node_is_rejected(self):
        self.assertEqual(self.post({**MSG, "node": "node-nope"}).status_code, 400)

    def test_bulk(self):
        make_node()
        payload = [{**MSG, "to": f"+1555000{i:04d}"} for i in range(5)]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/api/messages/bulk/", payload, format="json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()), 5)
        self.assertEqual(SmsMessage.objects.filter(status=Status.DISPATCHED).count(), 5)

    def test_bulk_validation_is_all_or_nothing_and_capped(self):
        bad = [MSG, {**MSG, "to": "nope"}]
        self.assertEqual(
            self.client.post("/api/messages/bulk/", bad, format="json").status_code, 400
        )
        self.assertEqual(SmsMessage.objects.count(), 0)
        self.assertEqual(
            self.client.post("/api/messages/bulk/", [], format="json").status_code, 400
        )
        self.assertEqual(
            self.client.post("/api/messages/bulk/", MSG, format="json").status_code, 400
        )
        with override_settings(MESSAGE_BULK_MAX=2):
            response = self.client.post("/api/messages/bulk/", [MSG] * 3, format="json")
        self.assertEqual(response.status_code, 400)

    def test_list_filter_and_scoping(self):
        node = make_node()
        mine_sent = make_message(self.user, status=Status.SENT, node=node)
        make_message(self.user, status=Status.PENDING)
        make_message(make_user())  # someone else's

        everything = self.client.get("/api/messages/").json()
        self.assertEqual(everything["count"], 2)
        by_status = self.client.get("/api/messages/?status=sent").json()
        self.assertEqual([m["id"] for m in by_status["results"]], [str(mine_sent.id)])
        by_node = self.client.get(f"/api/messages/?node={node.node_id}").json()
        self.assertEqual(by_node["count"], 1)
        by_to = self.client.get("/api/messages/?to=%2B15559999999").json()
        self.assertEqual(by_to["count"], 0)
        future = (timezone.now() + timedelta(days=1)).isoformat()
        self.assertEqual(
            self.client.get("/api/messages/", {"created_after": future}).json()[
                "count"
            ],
            0,
        )
        self.assertEqual(
            self.client.get("/api/messages/?page_size=1").json()["next"] is not None,
            True,
        )

    def test_cannot_read_someone_elses_message(self):
        other = make_message(make_user())
        self.assertEqual(self.client.get(f"/api/messages/{other.id}/").status_code, 404)
        self.assertEqual(
            self.client.post(f"/api/messages/{other.id}/cancel/").status_code, 404
        )

    def test_cancel(self):
        pending = make_message(self.user)
        response = self.client.post(f"/api/messages/{pending.id}/cancel/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")

        node = make_node()
        unacked = make_message(self.user, node=node, status=Status.DISPATCHED)
        self.assertEqual(
            self.client.post(f"/api/messages/{unacked.id}/cancel/").status_code, 200
        )

        acked = make_message(
            self.user, node=node, status=Status.QUEUED, acked_at=timezone.now()
        )
        self.assertEqual(
            self.client.post(f"/api/messages/{acked.id}/cancel/").status_code, 409
        )
        again = self.client.post(f"/api/messages/{pending.id}/cancel/")
        self.assertEqual(again.status_code, 409)

    def test_swagger_schema_documents_endpoints(self):
        schema = APIClient().get("/api/schema/?format=json").json()
        for path in (
            "/api/nodes/register",
            "/api/nodes/",
            "/api/nodes/{node_id}/config/",
            "/api/messages/",
            "/api/messages/bulk/",
            "/api/messages/{id}/cancel/",
            "/api/api-keys/",
        ):
            self.assertIn(path, schema["paths"])
        self.assertIn("ApiKeyAuth", schema["components"]["securitySchemes"])


@override_settings(MESSAGE_ACK_TIMEOUT_SEC=60)
class WebhookTests(TestCase):
    def test_status_change_posts_to_callback_url(self):
        from unittest import mock

        node = make_node()
        message = make_message(
            node=node, status=Status.DISPATCHED, callback_url="https://example.com/hook"
        )
        with mock.patch("messaging.tasks.urllib.request.urlopen") as urlopen:
            with self.captureOnCommitCallbacks(execute=True):
                services.apply_node_status(
                    node, {"id": str(message.id), "status": "sent", "attempts": 1}
                )
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://example.com/hook")
        self.assertIn(b'"status": "sent"', request.data)


# ---------------------------------------------------------- end to end (WS)


class EndToEndTests(WebSocketCase):
    async def post_message(self, client, **extra):
        return await sync_to_async(client.post)(
            "/api/messages/", {**MSG, **extra}, format="json"
        )

    async def status_of(self, message_id):
        return (await SmsMessage.objects.aget(pk=message_id)).status

    async def test_full_flow_api_to_node_and_back(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)

        response = await self.post_message(client, sim_slot=0)
        self.assertEqual(response.status_code, 201)
        job = await comm.receive_json_from(timeout=2)
        self.assertEqual(
            job,
            {
                "type": "send_sms",
                "id": response.json()["id"],
                "to": MSG["to"],
                "body": MSG["body"],
                # The dispatcher names the SIM lane it picked.
                "simSlot": 0,
                "subscriptionId": 3,
            },
        )

        await comm.send_json_to({"type": "ack", "id": job["id"], "accepted": True})
        await self.eventually(
            lambda: SmsMessage.objects.get(pk=job["id"]).status == Status.QUEUED
        )

        base = {
            "type": "status_update",
            "id": job["id"],
            "attempts": 1,
            "timestamp": 1760000000000,
        }
        await comm.send_json_to({**base, "status": "sending"})
        await comm.send_json_to({**base, "status": "sent"})
        await comm.send_json_to({**base, "status": "sent"})  # duplicate
        await comm.send_json_to({**base, "status": "delivered"})
        await comm.send_json_to({**base, "status": "sent"})  # stale after terminal
        await comm.send_json_to(
            {**base, "status": "failed", "errorCode": 1}
        )  # ignored too

        await self.eventually(lambda: SmsMessage.objects.get(pk=job["id"]).delivered_at)
        await asyncio.sleep(0.2)  # let the trailing stale frames be processed
        body = (await sync_to_async(client.get)(f"/api/messages/{job['id']}/")).json()
        self.assertEqual(body["status"], "delivered")
        self.assertIsNone(body["failed_at"])
        self.assertIsNone(body["error_code"])
        self.assertEqual(body["node"], node.node_id)
        for field in ("dispatched_at", "acked_at", "sent_at", "delivered_at"):
            self.assertIsNotNone(body[field], field)

    async def test_pending_messages_are_sent_when_a_node_connects(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node, token = await sync_to_async(register)()
        response = await self.post_message(client)
        self.assertEqual(response.json()["status"], "pending")
        comm = await self.connect_node(node.node_id, token)
        job = await comm.receive_json_from(timeout=2)
        self.assertEqual(job["id"], response.json()["id"])

    async def test_unacked_jobs_are_resent_after_reconnect(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        job_id = (await self.post_message(client)).json()["id"]
        first = await comm.receive_json_from(timeout=2)
        self.assertEqual(first["id"], job_id)
        await comm.disconnect()  # never acked
        await self.eventually(lambda: not Node.objects.get(pk=node.pk).connected)

        comm = self.open(node.node_id, token)
        await comm.connect()
        self.assertEqual((await comm.receive_json_from(timeout=2))["type"], "config")
        again = await comm.receive_json_from(timeout=2)
        self.assertEqual(again["id"], job_id)  # same id: the node dedupes

    async def test_acked_jobs_are_not_resent(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node, token = await sync_to_async(register)()
        comm = await self.connect_node(node.node_id, token)
        job_id = (await self.post_message(client)).json()["id"]
        await comm.receive_json_from(timeout=2)
        await comm.send_json_to({"type": "ack", "id": job_id, "accepted": True})
        await self.eventually(lambda: SmsMessage.objects.get(pk=job_id).acked_at)
        await comm.disconnect()
        await self.eventually(lambda: not Node.objects.get(pk=node.pk).connected)
        comm = self.open(node.node_id, token)
        await comm.connect()
        await comm.receive_json_from(timeout=2)  # config
        self.assertTrue(await comm.receive_nothing(timeout=0.3))

    async def test_rejected_ack_redispatches_to_another_node(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node_a, token_a = await sync_to_async(register)()
        node_b, token_b = await sync_to_async(register)()
        comm_a = await self.connect_node(node_a.node_id, token_a)
        comm_b = await self.connect_node(node_b.node_id, token_b)
        # Make A the preferred target.
        await sync_to_async(Node.objects.filter(pk=node_b.pk).update)(queue_depth=10)

        job_id = (await self.post_message(client)).json()["id"]
        job = await comm_a.receive_json_from(timeout=2)
        self.assertEqual(job["id"], job_id)

        await comm_a.send_json_to(
            {"type": "ack", "id": job_id, "accepted": False, "reason": "queue_full"}
        )
        moved = await comm_b.receive_json_from(timeout=2)
        self.assertEqual(moved["id"], job_id)
        row = await SmsMessage.objects.select_related("node").aget(pk=job_id)
        self.assertEqual(row.node.node_id, node_b.node_id)
        self.assertEqual(row.status, Status.DISPATCHED)
        self.assertEqual(row.dispatch_attempts, 2)
        self.assertTrue(await comm_a.receive_nothing(timeout=0.2))

    async def test_status_from_a_node_that_does_not_own_the_job_is_ignored(self):
        user = await sync_to_async(make_user)()
        client = await sync_to_async(api_client_for)(user)
        node_a, token_a = await sync_to_async(register)()
        node_b, token_b = await sync_to_async(register)()
        comm_a = await self.connect_node(node_a.node_id, token_a)
        comm_b = await self.connect_node(node_b.node_id, token_b)
        await sync_to_async(Node.objects.filter(pk=node_b.pk).update)(queue_depth=10)
        job_id = (await self.post_message(client)).json()["id"]
        await comm_a.receive_json_from(timeout=2)
        await comm_b.send_json_to(
            {
                "type": "status_update",
                "id": job_id,
                "status": "delivered",
                "attempts": 1,
                "timestamp": 1,
            }
        )
        await asyncio.sleep(0.2)
        self.assertEqual(await self.status_of(job_id), Status.DISPATCHED)
