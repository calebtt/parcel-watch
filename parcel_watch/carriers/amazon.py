"""Amazon's public tracker (track.amazon.com).

Undocumented: this is the JSON endpoint behind the public tracking page. It
answers for Amazon Shipping parcels; many retail-order TBA numbers are only
visible inside the buyer's Amazon account, and come back as not found.
Set PARCEL_WATCH_AMAZON=off to stop using it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from .. import net
from ..detect import tracking_url
from ..model import Eta, Event, TrackResult, join_location
from .base import Provider, ProviderError, describe_http_error

URL = "https://track.amazon.com/api/tracker/{}"

NOT_FOUND_CODES = {"TRACKING_ID_NOT_FOUND", "INVALID_TRACKING_ID"}

# Newer locale data puts U+202F before AM/PM.
NARROW_NBSP = "\u202f"

RETAIL_NOTE = (
    "Amazon's public tracker has no record of this number. Retail-order TBA numbers are often "
    "visible only in the buyer's Amazon account (Your Orders) or the shipping email."
)

# Checked in order against the upper-cased status code.
STATUS_RULES = (
    (("RETURN",), "returned"),
    (("ATTEMPT", "UNDELIVERABLE", "UNABLE", "REJECT", "REFUSED", "DAMAGED", "LOST", "CANCEL", "ADDRESS"), "exception"),
    (("PICKUP_READY", "READY_FOR_PICKUP", "HOLD_FOR_PICKUP", "AWAITING_CUSTOMER_PICKUP"), "available_for_pickup"),
    (("OUT_FOR_DELIVERY", "OFD"), "out_for_delivery"),
    (("DELIVERED",), "delivered"),
    (("CREATION_CONFIRMED", "LABEL_CREATED", "SHIPMENT_CREATED", "INFORMATION_RECEIVED"), "label_created"),
)

EVENT_TEXT = {
    "CreationConfirmed": "Shipping label created",
    "PickupDone": "Picked up",
    "Received": "Received at facility",
    "ArrivedAtSortCenter": "Arrived at sort center",
    "ArrivedAtDeliveryCenter": "Arrived at delivery station",
    "Departed": "Departed facility",
    "InTransit": "In transit",
    "OutForDelivery": "Out for delivery",
    "DeliveryAttempted": "Delivery attempted",
    "Delivered": "Delivered",
    "Held": "Held",
    "Returned": "Returned",
}


class Amazon(Provider):
    name = "amazon-public"
    carriers = ("amazon",)

    def configured(self) -> bool:
        return (self.config.get("PARCEL_WATCH_AMAZON") or "on").lower() not in ("0", "off", "false", "no")

    def missing(self) -> list[str]:
        return [] if self.configured() else ["PARCEL_WATCH_AMAZON (set to off)"]

    def track(self, number: str) -> TrackResult:
        resp = net.request("GET", URL.format(number))
        body = resp.json()
        if resp.status != 200 or not isinstance(body, dict):
            raise ProviderError("Amazon tracker failed, " + describe_http_error(resp))
        return parse(number, body)


def _maybe_json(value: Any) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _date_value(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        value = value.get("date") or value.get("stringValue")
    return value if isinstance(value, str) and value.strip() else None


def parse_time(value: Optional[str]) -> Optional[str]:
    """'Aug 11, 2026, 4:31:56 PM' (local, no zone) -> '2026-08-11T16:31:56'."""
    if not value:
        return None
    text = value.replace(NARROW_NBSP, " ").strip()
    try:
        return datetime.fromisoformat(text).isoformat()
    except ValueError:
        pass
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%B %d, %Y, %I:%M:%S %p", "%b %d, %Y %I:%M:%S %p", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    return value


def classify(code: str) -> Optional[str]:
    upper = code.upper().replace(" ", "_")
    for needles, status in STATUS_RULES:
        if any(n in upper for n in needles):
            return status
    return None


def parse(number: str, body: dict) -> TrackResult:
    result = TrackResult(
        tracking_number=number,
        carrier="amazon",
        source=Amazon.name,
        tracking_url=tracking_url("amazon", number),
    )
    tracker = _maybe_json(body.get("progressTracker"))
    codes = {(e or {}).get("errorCode") for e in tracker.get("errors") or []}
    if codes & NOT_FOUND_CODES:
        result.status = "not_found"
        result.note = RETAIL_NOTE
        return result

    summary = tracker.get("summary") or {}
    metadata = summary.get("metadata") or {}
    code = (metadata.get("trackingStatus") or {}).get("stringValue") or summary.get("status") or ""
    result.status_text = summary.get("status") or code or None
    result.delayed = bool(tracker.get("hasDeliveryDelayed"))

    history = _maybe_json(body.get("eventHistory")).get("eventHistory") or []
    for ev in sorted(history, key=lambda e: parse_time(e.get("eventTime")) or "", reverse=True):
        loc = ev.get("location") or {}
        event_code = ev.get("eventCode") or ""
        event = Event(
            time=parse_time(ev.get("eventTime")),
            description=EVENT_TEXT.get(event_code, event_code),
            location=join_location(loc.get("city"), loc.get("stateProvince"), loc.get("countryCode")),
            code=event_code or None,
        )
        if result.events and result.events[-1] == event:
            continue  # the tracker repeats some events verbatim
        result.events.append(event)

    status = classify(code) if code else None
    if status is None and result.events:
        status = classify(result.events[0].code or "")
    if status is None:
        status = "in_transit" if (code or result.events) else "unknown"
    if "SHIPMENT_OLDER_THAN_SUPPORTED_AGE" in codes:
        status, result.note = "unknown", "Amazon no longer keeps history for this shipment."
    result.status = status

    if status == "delivered":
        result.delivered_at = parse_time(_date_value(metadata.get("deliveryDate"))) or (
            result.events[0].time if result.events else None
        )
    else:
        expected = _date_value(tracker.get("expectedDeliveryDate")) or _date_value(metadata.get("expectedDeliveryDate"))
        stamp = parse_time(expected)
        if stamp and stamp[:4].isdigit():
            result.eta = Eta(date=stamp[:10], end=stamp if len(stamp) > 10 else None, kind="estimated")
        elif expected:
            result.note = f"Amazon expects delivery: {expected}"
    return result
