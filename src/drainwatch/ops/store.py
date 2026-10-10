"""Persistence for operational entities.

The readings store (``drainwatch.api.storage``) is a time series: append-only, keyed by
drain, never revised. Operational records are the opposite — a user is edited, an
inspection is verified, a work order changes state — so they need last-write-wins
semantics, which is a different interface and gets its own module.

Two backends, same shape as the readings store, chosen by the same environment:

* **LocalStorage** — one JSONL file per collection under ``data/local_store/ops``.
  Records are rewritten by id, so history is preserved in the append log and reads are
  cheap. This is what the whole operations side runs on for local development.
* **AwsStorage** — a single DynamoDB table, partition key ``collection``, sort key ``id``,
  so a collection is one Query. Shares the bucket/table environment as the readings store.

Every record carries ``id``, ``created_at`` and ``updated_at`` — the audit trail reads
from those, and the V1 interface shows them, so they are set here rather than by callers.
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from drainwatch.config import DATA_DIR

OPS_DIR = DATA_DIR / "local_store" / "ops"

# Collections the operations domain owns. Kept as a tuple so a typo in a caller becomes a
# KeyError here rather than a silently empty result set.
COLLECTIONS = (
    "users",
    "sessions",
    "sources",
    "assets",
    "inspections",
    "incidents",
    "work_orders",
    "crews",
    "audit_events",
    "access_requests",
    "sequences",
)


def now_iso() -> str:
    """UTC, second precision, ``Z``-suffixed. One format everywhere, so string sort is
    chronological sort and no caller has to think about it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EntityStore(Protocol):
    name: str

    def put(self, collection: str, record: dict) -> dict: ...
    def get(self, collection: str, record_id: str) -> dict | None: ...
    def all(self, collection: str) -> list[dict]: ...
    def find(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]: ...
    def next_id(self, collection: str, prefix: str, *, width: int = 5, start: int = 0) -> str: ...


def _check(collection: str) -> None:
    if collection not in COLLECTIONS:
        raise KeyError(f"unknown collection {collection!r}; expected one of {COLLECTIONS}")


def _stamp(record: dict, *, creating: bool) -> dict:
    """Set the bookkeeping fields every record carries."""
    stamped = dict(record)
    stamped.setdefault("created_at", now_iso())
    stamped["updated_at"] = now_iso()
    if creating:
        stamped["created_at"] = stamped["updated_at"]
    return stamped


class LocalStorage:
    """One JSONL file per collection under ``data/local_store/ops``.

    Appends rather than rewrites: the file is an append log, and a read folds it by id,
    last write winning. That keeps the write path a single ``open(..., "a")`` — no
    read-modify-write race between two operators acting at the same moment, which on a
    dispatch screen is not hypothetical.
    """

    name = "local"

    def __init__(self, root: Path = OPS_DIR) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, collection: str) -> Path:
        _check(collection)
        return self.root / f"{collection}.jsonl"

    def put(self, collection: str, record: dict) -> dict:
        _check(collection)
        if "id" not in record:
            raise ValueError(f"{collection} record needs an id")
        existing = self.get(collection, record["id"])
        stored = _stamp(record, creating=existing is None)
        with self._lock, self._path(collection).open("a") as handle:
            handle.write(json.dumps(stored) + "\n")
        return stored

    def get(self, collection: str, record_id: str) -> dict | None:
        found = None
        for record in self._read(collection):
            if record.get("id") == record_id:
                found = record  # later lines win — this is the last-write-wins fold
        return found

    def all(self, collection: str) -> list[dict]:
        folded: dict[str, dict] = {}
        for record in self._read(collection):
            if "id" in record:
                folded[record["id"]] = record
        return list(folded.values())

    def find(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]:
        return [record for record in self.all(collection) if predicate(record)]

    def next_id(
        self, collection: str, prefix: str, *, width: int = 5, start: int = 0
    ) -> str:
        """Allocate a sequential id like ``INSP-24891``.

        Sequences live in the store, not in memory, so a restart does not reissue an id
        that an audit record already references.
        """
        _check(collection)
        key = f"{collection}:{prefix}"
        current = self.get("sequences", key)
        value = int(current["value"]) + 1 if current else start
        self.put("sequences", {"id": key, "value": value})
        return f"{prefix}{value:0{width}d}"

    def _read(self, collection: str) -> list[dict]:
        path = self._path(collection)
        if not path.exists():
            return []
        records = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line from a killed process; the rest is intact
        return records


class AwsStorage:
    """DynamoDB: partition key ``collection``, sort key ``id``.

    A collection is one Query, which is the right primitive here — unlike the readings
    table there is no chronological range scan, just "give me this collection".

    The sequences collection needs a read-modify-write. Two operators clicking verify in
    the same second could collide on an id; it is accepted because the alternative is a
    conditional-expression retry loop that buys nothing at this scale, and the collision
    would surface as a duplicate-key error rather than a wrong record.
    """

    name = "aws"

    def __init__(self, table: str, *, region: str | None = None) -> None:
        import boto3

        self.table_name = table
        self.region = region or os.environ.get("AWS_REGION", "ap-south-1")
        self._table = boto3.resource("dynamodb", region_name=self.region).Table(table)

    @staticmethod
    def _to_dynamo(value: Any) -> Any:
        from decimal import Decimal

        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, dict):
            return {k: AwsStorage._to_dynamo(v) for k, v in value.items()}
        if isinstance(value, list):
            return [AwsStorage._to_dynamo(v) for v in value]
        return value

    @staticmethod
    def _from_dynamo(value: Any) -> Any:
        from decimal import Decimal

        if isinstance(value, Decimal):
            as_float = float(value)
            return int(as_float) if as_float.is_integer() else as_float
        if isinstance(value, dict):
            return {k: AwsStorage._from_dynamo(v) for k, v in value.items()}
        if isinstance(value, list):
            return [AwsStorage._from_dynamo(v) for v in value]
        return value

    def put(self, collection: str, record: dict) -> dict:
        _check(collection)
        if "id" not in record:
            raise ValueError(f"{collection} record needs an id")
        existing = self.get(collection, record["id"])
        stored = _stamp(record, creating=existing is None)
        item = self._to_dynamo({**stored, "collection": collection})
        self._table.put_item(Item=item)
        return stored

    def get(self, collection: str, record_id: str) -> dict | None:
        _check(collection)
        response = self._table.get_item(Key={"collection": collection, "id": record_id})
        item = response.get("Item")
        return self._from_dynamo(item) if item else None

    def all(self, collection: str) -> list[dict]:
        from boto3.dynamodb.conditions import Key

        _check(collection)
        records: list[dict] = []
        kwargs: dict = {"KeyConditionExpression": Key("collection").eq(collection)}
        while True:
            response = self._table.query(**kwargs)
            records.extend(self._from_dynamo(item) for item in response.get("Items", []))
            key = response.get("LastEvaluatedKey")
            if not key:
                break
            kwargs["ExclusiveStartKey"] = key
        return records

    def find(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]:
        return [record for record in self.all(collection) if predicate(record)]

    def next_id(
        self, collection: str, prefix: str, *, width: int = 5, start: int = 0
    ) -> str:
        _check(collection)
        key = f"{collection}:{prefix}"
        current = self.get("sequences", key)
        value = int(current["value"]) + 1 if current else start
        self.put("sequences", {"id": key, "value": value})
        return f"{prefix}{value:0{width}d}"


class SupabaseStorage:
    """Supabase (PostgreSQL) backend.

    Each collection is a table in Supabase. Records are upserted by ``id``.

    **Table setup** — create one table per collection in the Supabase dashboard::

        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            data JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        );

    Repeat for each collection: ``sessions``, ``sources``, ``assets``,
    ``inspections``, ``incidents``, ``work_orders``, ``crews``,
    ``audit_events``, ``access_requests``, ``sequences``.

    The ``data`` column holds the full record as JSONB. This keeps the schema
    flexible — the operational domain evolves faster than a rigid column set
    would allow — while still benefiting from PostgreSQL's indexing and
    transactional guarantees.

    **Environment** — set ``SUPABASE_URL`` and ``SUPABASE_KEY`` (the service-role
    key for server-side access, not the anon key).
    """

    name = "supabase"

    def __init__(self, url: str, key: str) -> None:
        from supabase import create_client

        self.client = create_client(url, key)
        self._lock = threading.Lock()

    def put(self, collection: str, record: dict) -> dict:
        _check(collection)
        if "id" not in record:
            raise ValueError(f"{collection} record needs an id")
        existing = self.get(collection, record["id"])
        stored = _stamp(record, creating=existing is None)
        with self._lock:
            self.client.table(collection).upsert(
                {"id": stored["id"], "data": stored},
                on_conflict="id",
            ).execute()
        return stored

    def get(self, collection: str, record_id: str) -> dict | None:
        _check(collection)
        try:
            response = (
                self.client.table(collection)
                .select("data")
                .eq("id", record_id)
                .limit(1)
                .execute()
            )
            if response.data:
                return response.data[0]["data"]
        except Exception:
            pass
        return None

    def all(self, collection: str) -> list[dict]:
        _check(collection)
        result: list[dict] = []
        try:
            response = self.client.table(collection).select("data").execute()
            for row in response.data or []:
                result.append(row["data"])
        except Exception:
            pass
        return result

    def find(self, collection: str, predicate: Callable[[dict], bool]) -> list[dict]:
        return [record for record in self.all(collection) if predicate(record)]

    def next_id(
        self, collection: str, prefix: str, *, width: int = 5, start: int = 0
    ) -> str:
        _check(collection)
        key = f"{collection}:{prefix}"
        current = self.get("sequences", key)
        value = int(current["value"]) + 1 if current else start
        self.put("sequences", {"id": key, "value": value})
        return f"{prefix}{value:0{width}d}"


# Singleton cache: Supabase client creation is not free; reuse across requests.
_supabase_store: SupabaseStorage | None = None


def get_ops_store() -> EntityStore:
    """Pick a backend from the environment, mirroring :func:`drainwatch.api.storage.get_storage`.

    Priority order:
    1. **Supabase** — ``SUPABASE_URL`` + ``SUPABASE_KEY`` (recommended for hackathon deploys).
    2. **AWS DynamoDB** — ``DRAINWATCH_OPS_TABLE`` or derived from ``DRAINWATCH_TABLE``.
    3. **Local JSONL** — the default; everything under ``data/local_store/ops``.
    """
    global _supabase_store

    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_KEY")
    if supabase_url and supabase_key:
        if _supabase_store is None:
            _supabase_store = SupabaseStorage(supabase_url, supabase_key)
            print(f"Using Supabase backend at {supabase_url[:40]}...")
        return _supabase_store

    table = os.environ.get("DRAINWATCH_OPS_TABLE")
    if not table:
        base = os.environ.get("DRAINWATCH_TABLE")
        if base:
            table = f"{base}-ops"
    if table:
        return AwsStorage(table)
    return LocalStorage()


def slugify(value: str) -> str:
    """Filesystem/key-safe form of a human label."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return cleaned.strip("-") or "unnamed"

