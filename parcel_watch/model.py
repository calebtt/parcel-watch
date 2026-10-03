"""Carrier-neutral tracking result."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Normalized statuses, roughly in shipment order. "not_found" means the source
# answered and has no record of the number; "error" means we could not ask.
STATUSES = (
    "label_created",
    "in_transit",
    "out_for_delivery",
    "available_for_pickup",
    "delivered",
    "exception",
    "returned",
    "unknown",
    "not_found",
    "error",
)


@dataclass
class Eta:
    """When the carrier expects to deliver.

    `date` is the local delivery date (YYYY-MM-DD). `start`/`end` are ISO
    timestamps for a delivery window when the carrier gives one; `end` alone is
    a "by" time. `kind` says where the estimate came from: estimated,
    scheduled, rescheduled, commitment, appointment or standard_transit.
    """

    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    kind: str = "estimated"


@dataclass
class Event:
    time: Optional[str]
    description: str
    location: Optional[str] = None
    code: Optional[str] = None


@dataclass
class TrackResult:
    tracking_number: str
    carrier: str
    source: str
    status: str = "unknown"
    status_text: Optional[str] = None
    eta: Optional[Eta] = None
    delivered_at: Optional[str] = None
    delayed: bool = False
    events: list[Event] = field(default_factory=list)
    tracking_url: Optional[str] = None
    note: Optional[str] = None
    error: Optional[str] = None
    #: Sources asked before this answer, with what each said.
    tried: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: now_iso())

    @property
    def found(self) -> bool:
        return self.status not in ("not_found", "error")

    def to_dict(self, max_events: Optional[int] = None) -> dict:
        d = asdict(self)
        if max_events is not None:
            d["events"] = d["events"][:max_events]
        return d


def parse_iso(stamp: Optional[str]) -> Optional[datetime]:
    """fromisoformat that also takes a trailing Z (Python < 3.11 does not)."""
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp[:-1] + "+00:00" if stamp.endswith("Z") else stamp)
    except ValueError:
        return None


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def join_location(*parts: Optional[str]) -> Optional[str]:
    """City, state, country — never street lines or postal codes."""
    kept = [p.strip() for p in parts if p and p.strip()]
    return ", ".join(kept) or None
