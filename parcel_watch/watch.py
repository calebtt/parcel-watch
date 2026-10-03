"""Watch list: remembered packages, and what changed since the last check.

`check` is built for a scheduled routine: it refreshes each package that is
not delivered yet and reports only what changed (status, delivery estimate,
latest scan).
"""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import config
from .detect import normalize
from .model import TrackResult, now_iso, parse_iso

#: Delivered packages are dropped from the list this long after delivery.
KEEP_DELIVERED = timedelta(days=7)


def path() -> Path:
    return config.home() / "watchlist.json"


@contextmanager
def locked() -> Iterator[dict]:
    """Read-modify-write the watch list under an exclusive lock, so a routine
    and a chat command can't overwrite each other."""
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            data = {}
        data.setdefault("version", 1)
        data.setdefault("packages", {})
        yield data
        config.write_private(p, json.dumps(data, indent=2))


def read() -> dict:
    try:
        data = json.loads(path().read_text())
    except (OSError, ValueError):
        data = {}
    return data.get("packages") or {}


def snapshot(result: TrackResult) -> dict:
    latest = result.events[0] if result.events else None
    return {
        "carrier": result.carrier,
        "source": result.source,
        "status": result.status,
        "status_text": result.status_text,
        "eta": result.eta.__dict__ if result.eta else None,
        "delivered_at": result.delivered_at,
        "delayed": result.delayed,
        "latest_event": latest.__dict__ if latest else None,
        "tracking_url": result.tracking_url,
        "note": result.note,
    }


def describe_eta(eta: Optional[dict]) -> str:
    if not eta:
        return "none"
    text = eta.get("date") or "?"
    if eta.get("start") and eta.get("end"):
        text += f" {eta['start'][11:16]}-{eta['end'][11:16]}"
    elif eta.get("end"):
        text += f" by {eta['end'][11:16]}"
    return text


def diff(old: Optional[dict], new: dict) -> list[str]:
    """Human-readable changes between two snapshots."""
    if old is None:
        return ["now watching"]
    changes = []
    if old.get("status") != new.get("status"):
        changes.append(f"status {old.get('status')} -> {new.get('status')}")
    if describe_eta(old.get("eta")) != describe_eta(new.get("eta")) and new.get("status") != "delivered":
        changes.append(f"delivery estimate {describe_eta(old.get('eta'))} -> {describe_eta(new.get('eta'))}")
    if not old.get("delayed") and new.get("delayed"):
        changes.append("carrier reports a delay")
    old_ev, new_ev = old.get("latest_event"), new.get("latest_event")
    if new_ev and new_ev != old_ev:
        where = f" ({new_ev['location']})" if new_ev.get("location") else ""
        changes.append(f"new scan: {new_ev.get('description')}{where}")
    return changes


def add(number: str, label: Optional[str], carrier: Optional[str]) -> dict:
    n = normalize(number)
    with locked() as data:
        entry = data["packages"].setdefault(n, {"added_at": now_iso()})
        if label is not None:
            entry["label"] = label
        if carrier is not None:
            entry["carrier"] = carrier
        return dict(entry)


def remove(number: str) -> bool:
    with locked() as data:
        return data["packages"].pop(normalize(number), None) is not None


def record(number: str, result: TrackResult) -> list[str]:
    """Store a fresh result; return what changed. Errors keep the last good
    snapshot so a temporary outage doesn't look like a change."""
    n = normalize(number)
    with locked() as data:
        entry = data["packages"].get(n)
        if entry is None:
            return []
        entry["last_checked"] = result.fetched_at
        if result.status == "error":
            entry["last_error"] = result.error
            return []
        entry.pop("last_error", None)
        new = snapshot(result)
        changes = diff(entry.get("snapshot"), new)
        entry["snapshot"] = new
        if result.status == "delivered":
            entry.setdefault("delivered_seen_at", result.fetched_at)
        return changes


def _age(stamp: Optional[str], now: datetime) -> Optional[timedelta]:
    then = parse_iso(stamp)
    if then is None or then.tzinfo is None:
        return None
    return now - then


def check(
    track: Callable[[str, Optional[str]], TrackResult],
    *,
    min_age: timedelta = timedelta(minutes=15),
    force: bool = False,
) -> dict:
    now = datetime.now(timezone.utc)
    report = {"checked": 0, "skipped": 0, "changed": [], "errors": [], "pruned": []}

    with locked() as data:
        for n, entry in list(data["packages"].items()):
            age = _age(entry.get("delivered_seen_at"), now)
            if age is not None and age > KEEP_DELIVERED:
                del data["packages"][n]
                report["pruned"].append(n)
        due = []
        for n, entry in data["packages"].items():
            if entry.get("delivered_seen_at"):
                continue
            age = _age(entry.get("last_checked"), now)
            if not force and age is not None and age < min_age:
                report["skipped"] += 1
                continue
            due.append((n, entry.get("carrier"), entry.get("label")))

    # Network calls happen outside the lock; record() re-locks per package.
    for n, carrier, label in due:
        result = track(n, carrier)
        report["checked"] += 1
        changes = record(n, result)
        if result.status == "error":
            report["errors"].append({"tracking_number": n, "label": label, "error": result.error})
        elif changes:
            report["changed"].append(
                {"tracking_number": n, "label": label, "changes": changes, "current": snapshot(result)}
            )
    return report
