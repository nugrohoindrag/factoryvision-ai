from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from typing import Callable

from app.config import settings

log = logging.getLogger("fv.pubsub")

PLANT_EVENTS = "plant-events"
AGENT_COMMANDS = "agent-commands"

_handlers: dict[str, Callable[[dict], object]] = {}


def subscribe(topic: str, handler: Callable[[dict], object]) -> None:
    _handlers[topic] = handler


@lru_cache(maxsize=1)
def _publisher():
    from google.cloud import pubsub_v1

    return pubsub_v1.PublisherClient()


def publish(topic: str, message: dict) -> object:
    if settings.pubsub == "gcp":
        data = json.dumps(message, default=str).encode()
        future = _publisher().publish(_publisher().topic_path(settings.project, topic), data)
        if topic == AGENT_COMMANDS:
            future.result(timeout=10)
    handler = _handlers.get(topic)
    if handler is None:
        log.debug("no local handler for %s", topic)
        return None
    return handler(message)


def publish_batch(topic: str, messages: list[dict]) -> None:
    for m in messages:
        publish(topic, m)


def decode_push(body: dict) -> dict:
    data = body["message"]["data"]
    return json.loads(base64.b64decode(data))
