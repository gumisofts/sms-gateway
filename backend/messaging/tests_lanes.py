"""Per-SIM lanes: rotation, parts-based pacing, daily caps, auto-pause."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from nodes import services as node_services
from nodes.channels import send_sms_event
from nodes.factories import make_node, make_user

from . import dispatch, services
from .models import SmsMessage, Status
from .segments import count_parts
from .tests import MSG, make_message


class SegmentTests(TestCase):
    def test_gsm7(self):
        self.assertEqual(count_parts(""), 1)
        self.assertEqual(count_parts("a" * 160), 1)
        self.assertEqual(count_parts("a" * 161), 2)
        self.assertEqual(count_parts("a" * 306), 2)
        self.assertEqual(count_parts("a" * 307), 3)

    def test_extension_characters_count_twice(self):
        self.assertEqual(count_parts("€" * 80), 1)
        self.assertEqual(count_parts("€" * 81), 2)

    def test_unicode_amharic(self):
        self.assertEqual(count_parts("ሰላም" * 23), 1)  # 69 chars
        self.assertEqual(count_parts("ሰ" * 70), 1)
        self.assertEqual(count_parts("ሰ" * 71), 2)
        self.assertEqual(count_parts("ሰ" * 134), 2)
        self.assertEqual(count_parts("ሰ" * 135), 3)

    def test_emoji_takes_two_code_units(self):
        self.assertEqual(count_parts("😀" * 35), 1)
        self.assertEqual(count_parts("😀" * 36), 2)

    def test_create_message_estimates_parts(self):
        message, _ = services.create_message(make_user(), {**MSG, "body": "ሰ" * 100})
        self.assertEqual(message.parts, 2)


@override_settings(SIM_DEFAULT_RATE_PER_MINUTE=10, SIM_DEFAULT_DAILY_LIMIT=1000)
class LaneTests(TestCase):
    def setUp(self):
        self.owner = make_user()

    def dispatch(self, message):
        with self.captureOnCommitCallbacks(execute=True):
            result = dispatch.dispatch_message(message.id)
        message.refresh_from_db()
        return result

    def used(self, sim, parts=1, count=1, when=None):
        for _ in range(count):
            make_message(
                self.owner,
                node=sim.node,
                sim=sim,
                parts=parts,
                status=Status.SENT,
                dispatched_at=when or timezone.now(),
            )

    def test_dual_sim_node_rotates_between_its_sims(self):
        node = make_node(sims=((0, 3), (1, 4)))
        slots = []
        for _ in range(4):
            message = make_message(self.owner)
            self.assertTrue(self.dispatch(message))
            slots.append(message.sim.slot)
            # The node acked: it no longer counts as unacked load.
            SmsMessage.objects.filter(pk=message.pk).update(
                status=Status.SENT, acked_at=timezone.now()
            )
        self.assertEqual(sorted(slots), [0, 0, 1, 1])
        self.assertEqual(message.node, node)

    def test_send_sms_names_the_chosen_sim(self):
        make_node(sims=((0, 3), (1, 4)))
        message = make_message(self.owner, subscription_id=4)
        self.assertTrue(self.dispatch(message))
        payload = send_sms_event(message)["payload"]
        self.assertEqual((payload["simSlot"], payload["subscriptionId"]), (1, 4))

    def test_rate_counts_parts_per_sim(self):
        node = make_node(sims=((0, 3), (1, 4)))
        sim0, sim1 = node.sims.order_by("slot")
        sim0.rate_per_minute = 4
        sim0.save()
        self.used(sim0, parts=3)  # 3 of 4 parts used this minute
        message = make_message(self.owner, parts=2)
        self.assertTrue(self.dispatch(message))
        self.assertEqual(message.sim, sim1)

    def test_old_usage_does_not_count_against_the_minute(self):
        node = make_node(sims=((0, 3),))
        sim = node.sims.get()
        self.used(sim, count=10, when=timezone.now() - timedelta(minutes=2))
        self.assertTrue(self.dispatch(make_message(self.owner)))

    def test_all_sims_at_rate_leaves_message_pending(self):
        node = make_node(sims=((0, 3),))
        self.used(node.sims.get(), count=10)
        message = make_message(self.owner)
        self.assertFalse(self.dispatch(message))
        self.assertEqual(message.status, Status.PENDING)

    def test_oversized_message_still_fits_an_idle_lane(self):
        node = make_node(sims=((0, 3),))
        sim = node.sims.get()
        sim.rate_per_minute = 2
        sim.save()
        self.assertTrue(self.dispatch(make_message(self.owner, parts=5)))

    def test_daily_cap(self):
        node = make_node(sims=((0, 3), (1, 4)))
        sim0, sim1 = node.sims.order_by("slot")
        sim0.daily_limit = 5
        sim0.rate_per_minute = 0
        sim0.save()
        self.used(sim0, count=5, when=timezone.now() - timedelta(minutes=5))
        message = make_message(self.owner)
        self.assertTrue(self.dispatch(message))
        self.assertEqual(message.sim, sim1)
        sim1.is_enabled = False
        sim1.save()
        self.assertFalse(self.dispatch(make_message(self.owner)))

    def test_yesterday_does_not_count_against_the_daily_cap(self):
        node = make_node(sims=((0, 3),))
        sim = node.sims.get()
        sim.daily_limit = 5
        sim.save()
        yesterday = dispatch.day_start() - timedelta(minutes=1)
        self.used(sim, count=5, when=yesterday)
        self.assertTrue(self.dispatch(make_message(self.owner)))

    def test_paused_disabled_and_absent_sims_are_skipped(self):
        node = make_node(sims=((0, 3), (1, 4), (2, 5)))
        sim0, sim1, sim2 = node.sims.order_by("slot")
        sim0.paused_until = timezone.now() + timedelta(minutes=5)
        sim0.save()
        sim1.is_enabled = False
        sim1.save()
        message = make_message(self.owner)
        self.assertTrue(self.dispatch(message))
        self.assertEqual(message.sim, sim2)
        sim2.is_present = False
        sim2.save()
        self.assertFalse(self.dispatch(make_message(self.owner)))

    def test_expired_pause_is_ignored(self):
        node = make_node(sims=((0, 3),))
        sim = node.sims.get()
        sim.paused_until = timezone.now() - timedelta(seconds=1)
        sim.save()
        self.assertTrue(self.dispatch(make_message(self.owner)))

    def test_rejected_job_releases_the_sim(self):
        node = make_node(sims=((0, 3),))
        message = make_message(self.owner)
        self.dispatch(message)
        self.assertTrue(services.handle_ack(node, message.id, False, "queue_full"))
        message.refresh_from_db()
        self.assertIsNone(message.sim)
        self.assertEqual(message.status, Status.PENDING)

    def test_dispatch_pending_stops_when_lanes_are_full(self):
        node = make_node(sims=((0, 3),))
        self.used(node.sims.get(), count=10)
        for _ in range(dispatch.DISPATCH_MAX_MISSES + 10):
            make_message(self.owner)
        calls = []
        real = dispatch.dispatch_message

        def counting(message_id, *a, **kw):
            calls.append(message_id)
            return real(message_id, *a, **kw)

        dispatch.dispatch_message = counting
        try:
            self.assertEqual(dispatch.dispatch_pending(), 0)
        finally:
            dispatch.dispatch_message = real
        self.assertEqual(len(calls), dispatch.DISPATCH_MAX_MISSES)


@override_settings(SIM_PAUSE_AFTER_FAILURES=3, SIM_PAUSE_MINUTES=30)
class SimHealthTests(TestCase):
    def setUp(self):
        self.node = make_node(sims=((0, 3),))
        self.sim = self.node.sims.get()
        self.owner = make_user()

    def finish(self, status, **kw):
        message = make_message(
            self.owner, node=self.node, sim=self.sim, status=Status.SENDING, attempts=1
        )
        services.apply_node_status(
            self.node,
            {"id": str(message.id), "status": status, "attempts": 1, **kw},
        )
        self.sim.refresh_from_db()
        return message

    def test_consecutive_failures_pause_the_sim(self):
        for _ in range(2):
            self.finish("failed", errorCode=1, errorMessage="GENERIC_FAILURE")
        self.assertFalse(self.sim.is_paused())
        self.finish("failed", errorCode=1, errorMessage="GENERIC_FAILURE")
        self.assertTrue(self.sim.is_paused())
        self.assertIn("3 failed messages in a row", self.sim.pause_reason)
        self.assertIn("GENERIC_FAILURE", self.sim.pause_reason)
        self.assertAlmostEqual(
            (self.sim.paused_until - timezone.now()).total_seconds(), 30 * 60, delta=5
        )

    def test_success_resets_the_streak(self):
        self.finish("failed", errorCode=1)
        self.finish("failed", errorCode=1)
        self.finish("sent")
        self.assertEqual(self.sim.consecutive_failures, 0)
        self.assertIsNotNone(self.sim.last_success_at)
        self.finish("failed", errorCode=1)
        self.assertFalse(self.sim.is_paused())

    def test_resume_clears_pause(self):
        for _ in range(3):
            self.finish("failed", errorCode=1)
        services.resume_sim(self.sim)
        self.sim.refresh_from_db()
        self.assertFalse(self.sim.is_paused())
        self.assertEqual(self.sim.consecutive_failures, 0)

    @override_settings(SIM_PAUSE_AFTER_FAILURES=0)
    def test_auto_pause_can_be_disabled(self):
        for _ in range(10):
            self.finish("failed", errorCode=1)
        self.assertFalse(self.sim.is_paused())

    def test_node_reported_parts_replace_the_estimate(self):
        message = self.finish("sent", parts=3)
        message.refresh_from_db()
        self.assertEqual(message.parts, 3)

    def test_bogus_parts_are_ignored(self):
        for bogus in (0, -1, 1000, "3"):
            message = self.finish("sent", parts=bogus)
            message.refresh_from_db()
            self.assertEqual(message.parts, 1)


class NodeSimSyncTests(TestCase):
    def test_missing_sim_is_kept_and_marked_absent(self):
        node = make_node(sims=((0, 3), (1, 4)))
        sim1 = node.sims.get(slot=1)
        sim1.daily_limit = 50
        sim1.save()
        node_services.sync_sims(node, [{"slot": 0, "subscriptionId": 3}])
        sim1.refresh_from_db()
        self.assertFalse(sim1.is_present)
        node_services.sync_sims(
            node,
            [{"slot": 0, "subscriptionId": 3}, {"slot": 1, "subscriptionId": 4}],
        )
        sim1.refresh_from_db()
        self.assertTrue(sim1.is_present)
        self.assertEqual(sim1.daily_limit, 50)

    def test_swapped_sim_resets_health(self):
        node = make_node(sims=((0, 3),))
        sim = node.sims.get()
        sim.paused_until = timezone.now() + timedelta(hours=1)
        sim.consecutive_failures = 7
        sim.save()
        node_services.sync_sims(node, [{"slot": 0, "subscriptionId": 9}])
        sim.refresh_from_db()
        self.assertEqual(sim.subscription_id, 9)
        self.assertIsNone(sim.paused_until)
        self.assertEqual(sim.consecutive_failures, 0)

    def test_heartbeat_records_sms_limit(self):
        node = make_node()
        node_services.record_heartbeat(
            node.pk,
            {"batteryPercent": 80, "smsLimit": {"raised": True, "maxCount": 100000}},
        )
        node.refresh_from_db()
        self.assertTrue(node.sms_limit_raised)
        self.assertEqual(node.sms_limit_max_count, 100000)
        node_services.record_heartbeat(node.pk, {"smsLimit": {"raised": None}})
        node.refresh_from_db()
        self.assertIsNone(node.sms_limit_raised)
