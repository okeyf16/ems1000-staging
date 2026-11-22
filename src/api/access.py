"""Who is calling the API: Microsoft Entra sign-in (App Service authentication) or an API key.

App Service authentication ("Easy Auth") signs people in with the tenant's Microsoft accounts
and passes the signed-in person to the app in X-MS-CLIENT-PRINCIPAL* headers. App Service
removes those headers from incoming requests, so they can only come from its own sign-in;
the app trusts them only when ENTRA_SIGNIN=true (set by Terraform alongside the sign-in).

Every accepted call is counted in the ApiAccess table: one row per caller per UTC day.
Writes are batched per caller (at most one table write a minute per worker), so busy
callers don't add a table write to every request.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

FLUSH_S = 60.0
MAX_DAYS = 7


@dataclass(frozen=True)
class Caller:
    key: str        # stable id: Entra object id, or "key-primary" / "key-secondary"
    who: str        # what people see: display name or e-mail, or "API key (primary)"
    kind: str       # "entra" or "api-key"


def entra_enabled() -> bool:
    return os.getenv("ENTRA_SIGNIN", "").strip().lower() == "true"


def entra_caller(headers) -> Caller | None:
    """The person App Service signed in, or None (sign-in off or not signed in)."""
    if not entra_enabled():
        return None
    oid = (headers.get("X-MS-CLIENT-PRINCIPAL-ID") or "").strip()
    if not oid:
        return None
    name = (headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or "").strip()
    display = _display_name(headers.get("X-MS-CLIENT-PRINCIPAL"))
    who = f"{display} ({name})" if display and name and display != name else (display or name or oid)
    return Caller(key=_safe_key(oid), who=who[:120], kind="entra")


def key_caller(index: int) -> Caller:
    label = ["primary", "secondary"][index] if index < 2 else f"key {index + 1}"
    return Caller(key=f"key-{label.replace(' ', '-')}", who=f"API key ({label})", kind="api-key")


def _display_name(encoded: str | None) -> str | None:
    """The 'name' claim from the base64 JSON in X-MS-CLIENT-PRINCIPAL."""
    if not encoded:
        return None
    try:
        data = json.loads(base64.b64decode(encoded + "=" * (-len(encoded) % 4)))
        for claim in data.get("claims", []):
            if claim.get("typ") == "name":
                return str(claim.get("val") or "").strip() or None
    except (ValueError, TypeError, AttributeError):
        return None
    return None


def _safe_key(value: str) -> str:
    """Table RowKeys can't contain / \\ # ? or control characters."""
    return re.sub(r"[/\\#?\x00-\x1f\x7f]", "_", value)[:200]


def utc_day(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


class AccessRecorder:
    """Counts calls per caller per UTC day in the ApiAccess table."""

    def __init__(self, table, clock=time.time, flush_s: float = FLUSH_S):
        self.table = table
        self.clock = clock
        self.flush_s = flush_s
        self._pending: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def record(self, caller: Caller, endpoint: str) -> None:
        if self.table is None:
            return
        now = self.clock()
        day = utc_day(datetime.fromtimestamp(now, timezone.utc))
        with self._lock:
            p = self._pending.setdefault((day, caller.key), {"calls": 0, "flushed_at": 0.0})
            p.update(who=caller.who, kind=caller.kind, last_seen=now, last_endpoint=endpoint[:200])
            p["calls"] += 1
            due = now - p["flushed_at"] >= self.flush_s
        if due:
            self._flush_one(day, caller.key)

    def flush_all(self) -> None:
        with self._lock:
            keys = [k for k, p in self._pending.items() if p["calls"]]
        for day, key in keys:
            self._flush_one(day, key)

    def _flush_one(self, day: str, key: str) -> None:
        with self._lock:
            p = self._pending.get((day, key))
            if not p or not p["calls"]:
                return
            calls, p["calls"], p["flushed_at"] = p["calls"], 0, self.clock()
            snapshot = dict(p)
        try:
            try:
                existing = self.table.get_entity(partition_key=day, row_key=key)
                total = int(existing.get("calls", 0)) + calls
            except Exception:  # noqa: BLE001 - first call of the day for this caller
                total = calls
            self.table.upsert_entity(entity={
                "PartitionKey": day, "RowKey": key,
                "who": snapshot["who"], "kind": snapshot["kind"], "calls": total,
                "last_seen": datetime.fromtimestamp(snapshot["last_seen"], timezone.utc).isoformat(),
                "last_endpoint": snapshot["last_endpoint"],
            })
        except Exception:  # noqa: BLE001 - counting must never break an API call
            logger.exception("Could not record API access for %s", key)
            with self._lock:
                self._pending[(day, key)]["calls"] += calls

    def summary(self, days: int = 1) -> list[dict]:
        """Callers over the last `days` UTC days (today included), busiest-recent first."""
        if self.table is None:
            return []
        self.flush_all()
        days = max(1, min(MAX_DAYS, days))
        today = datetime.fromtimestamp(self.clock(), timezone.utc)
        merged: dict[str, dict] = {}
        for n in range(days):
            day = utc_day(today - timedelta(days=n))
            for row in self.table.query_entities(query_filter="PartitionKey eq @pk", parameters={"pk": day}):
                m = merged.setdefault(row["RowKey"], {"who": row.get("who"), "kind": row.get("kind"),
                                                      "calls": 0, "last_seen": "", "last_endpoint": None})
                m["calls"] += int(row.get("calls", 0))
                if str(row.get("last_seen", "")) > m["last_seen"]:
                    m.update(last_seen=str(row.get("last_seen")), last_endpoint=row.get("last_endpoint"),
                             who=row.get("who"), kind=row.get("kind"))
        return sorted(merged.values(), key=lambda m: m["last_seen"], reverse=True)
