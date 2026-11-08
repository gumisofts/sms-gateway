"""Small helpers shared by the test modules."""

import itertools

from django.contrib.auth import get_user_model

from nodes import services
from nodes.models import Node, NodeSim

_counter = itertools.count(1)

REGISTER_PAYLOAD = {
    "deviceName": "Pixel 7",
    "manufacturer": "Google",
    "model": "Pixel 7",
    "androidVersion": "15",
    "sdkInt": 35,
    "appVersion": "0.1.0",
    "sims": [{"slot": 0, "subscriptionId": 3, "carrier": "Vodafone"}],
}


def make_user(staff=False, **extra):
    n = next(_counter)
    return get_user_model().objects.create_user(
        username=f"user{n}",
        phone_number=f"7{n:08d}",
        password="pass12345",
        is_staff=staff,
        **extra,
    )


def make_node(connected=True, sims=((0, 3),), **fields):
    """A node row without going through registration."""
    fields.setdefault("device_name", "Test phone")
    node = Node.objects.create(connected=connected, **fields)
    for slot, subscription_id in sims:
        NodeSim.objects.create(
            node=node, slot=slot, subscription_id=subscription_id, carrier="Test"
        )
    return node


def register(**overrides):
    """Register through the service; returns (node, plaintext token)."""
    return services.register_node({**REGISTER_PAYLOAD, **overrides})
