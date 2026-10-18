from __future__ import annotations

import copy
import threading
from functools import lru_cache
from typing import Any, Protocol

from app.config import settings

Doc = dict[str, Any]


class Store(Protocol):
    def get(self, collection: str, doc_id: str) -> Doc | None: ...
    def set(self, collection: str, doc_id: str, doc: Doc) -> None: ...
    def update(self, collection: str, doc_id: str, fields: Doc) -> None: ...
    def delete(self, collection: str, doc_id: str) -> None: ...
    def where(self, collection: str, field: str, value: object) -> list[Doc]: ...


class MemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Doc]] = {}
        self._lock = threading.Lock()

    def get(self, collection: str, doc_id: str) -> Doc | None:
        with self._lock:
            doc = self._data.get(collection, {}).get(doc_id)
            return copy.deepcopy(doc) if doc is not None else None

    def set(self, collection: str, doc_id: str, doc: Doc) -> None:
        with self._lock:
            self._data.setdefault(collection, {})[doc_id] = copy.deepcopy(doc)

    def update(self, collection: str, doc_id: str, fields: Doc) -> None:
        with self._lock:
            self._data.setdefault(collection, {}).setdefault(doc_id, {}).update(copy.deepcopy(fields))

    def delete(self, collection: str, doc_id: str) -> None:
        with self._lock:
            self._data.get(collection, {}).pop(doc_id, None)

    def where(self, collection: str, field: str, value: object) -> list[Doc]:
        with self._lock:
            return [copy.deepcopy(d) for d in self._data.get(collection, {}).values() if d.get(field) == value]


class FirestoreStore:
    def __init__(self) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=settings.project or None)
        self._filter = firestore.FieldFilter

    def _ref(self, collection: str, doc_id: str) -> Any:
        return self._db.collection(collection).document(doc_id.replace("/", "__"))

    def get(self, collection: str, doc_id: str) -> Doc | None:
        snap = self._ref(collection, doc_id).get()
        return dict(snap.to_dict() or {}) if snap.exists else None

    def set(self, collection: str, doc_id: str, doc: Doc) -> None:
        self._ref(collection, doc_id).set(doc)

    def update(self, collection: str, doc_id: str, fields: Doc) -> None:
        self._ref(collection, doc_id).set(fields, merge=True)

    def delete(self, collection: str, doc_id: str) -> None:
        self._ref(collection, doc_id).delete()

    def where(self, collection: str, field: str, value: object) -> list[Doc]:
        q = self._db.collection(collection).where(filter=self._filter(field, "==", value))
        return [dict(s.to_dict() or {}) for s in q.stream()]


@lru_cache(maxsize=1)
def store() -> Store:
    return FirestoreStore() if settings.store == "firestore" else MemoryStore()
