from __future__ import annotations

import copy
import threading
from functools import lru_cache
from typing import Protocol

from app.config import settings


class Store(Protocol):
    def get(self, collection: str, doc_id: str) -> dict | None: ...
    def set(self, collection: str, doc_id: str, doc: dict) -> None: ...
    def update(self, collection: str, doc_id: str, fields: dict) -> None: ...
    def delete(self, collection: str, doc_id: str) -> None: ...
    def where(self, collection: str, field: str, value) -> list[dict]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.Lock()

    def get(self, collection: str, doc_id: str) -> dict | None:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, collection: str, doc_id: str, doc: dict) -> None:
        with self._lock:
            self._data.setdefault(collection, {})[doc_id] = copy.deepcopy(doc)

    def update(self, collection: str, doc_id: str, fields: dict) -> None:
        with self._lock:
            self._data.setdefault(collection, {}).setdefault(doc_id, {}).update(copy.deepcopy(fields))

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)

    def where(self, collection: str, field: str, value) -> list[dict]:
        with self._lock:
            return [copy.deepcopy(d) for d in self._data.get(collection, {}).values() if d.get(field) == value]


class FirestoreStore:

    def __init__(self) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=settings.project or None)
        self._filter = firestore.FieldFilter

    def _ref(self, collection: str, doc_id: str):
        return self._db.collection(collection).document(doc_id.replace("/", "__"))

    def get(self, collection: str, doc_id: str) -> dict | None:
        snap = self._ref(collection, doc_id).get()
        return snap.to_dict() if snap.exists else None

    def set(self, collection: str, doc_id: str, doc: dict) -> None:
        self._ref(collection, doc_id).set(doc)

    def update(self, collection: str, doc_id: str, fields: dict) -> None:
        self._ref(collection, doc_id).set(fields, merge=True)

    def delete(self, collection: str, doc_id: str) -> None:
        self._ref(collection, doc_id).delete()

    def where(self, collection: str, field: str, value) -> list[dict]:
        q = self._db.collection(collection).where(filter=self._filter(field, "==", value))
        return [s.to_dict() for s in q.stream()]


@lru_cache(maxsize=1)
def store() -> Store:
    return FirestoreStore() if settings.store == "firestore" else MemoryStore()
