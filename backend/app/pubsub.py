from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from app.config import settings

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("fv.pubsub")

PLANT_EVENTS = "plant-events"
AGENT_COMMANDS = "agent-commands"

Message = dict[str, Any]

_handlers: dict[str, Callable[[Message], object]] = {}


def subscribe(topic: str, handler: Callable[[Message], object]) -> None:
    _handlers[topic] = handler


@lru_cache(maxsize=1)
def _publisher() -> Any:
    from google.cloud.pubsub_v1 import PublisherClient

    return PublisherClient()


def publish(topic: str, message: Message) -> object:
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


def publish_batch(topic: str, messages: list[Message]) -> None:
    for m in messages:
        publish(topic, m)


def decode_push(body: Message) -> Message:
    decoded: Message = json.loads(base64.b64decode(body["message"]["data"]))
    return decoded
