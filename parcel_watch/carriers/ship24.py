"""Ship24 tracking API: a fallback for carriers without a direct provider
(USPS, DHL, OnTrac, regional carriers) or without configured credentials.

Each new tracking number counts against the Ship24 plan, so the router asks
it last.
"""

from __future__ import annotations

from typing import Optional

from .. import net
from ..detect import tracking_url
from ..model import Eta, Event, TrackResult
from .base import Provider, ProviderError, describe_http_error

URL = "https://api.ship24.com/public/v1/trackers/track"

MILESTONES = {
    "pending": "unknown",
    "info_received": "label_created",
    "in_transit": "in_transit",
    "out_for_delivery": "out_for_delivery",
    "failed_attempt": "exception",
    "available_for_pickup": "available_for_pickup",
    "delivered": "delivered",
    "exception": "exception",
}


def carrier_from_courier(code: Optional[str]) -> Optional[str]:
    if not code:
        return None
    code = code.lower()
    if code in ("us-post", "usps"):
        return "usps"
    for name in ("fedex", "ups", "amazon", "dhl"):
        if code.startswith(name):
            return name
    return code


class Ship24(Provider):
    name = "ship24"
    carriers = ("*",)
    required = ("SHIP24_API_KEY",)

    def track(self, number: str, carrier_hint: Optional[str] = None) -> TrackResult:
        resp = net.request(
            "POST",
            URL,
            headers={"Authorization": f"Bearer {self.config.get('SHIP24_API_KEY')}"},
            json_body={"trackingNumber": number},
            timeout=75,  # Ship24 fetches from the courier synchronously, up to a minute
        )
        body = resp.json() or {}
        if resp.status not in (200, 201):
            raise ProviderError("Ship24 tracking failed, " + describe_http_error(resp, body.get("errors")))
        return parse(number, body, carrier_hint)


def parse(number: str, body: dict, carrier_hint: Optional[str] = None) -> TrackResult:
    trackings = (body.get("data") or {}).get("trackings") or []
    tracking = trackings[0] if trackings else {}
    shipment = tracking.get("shipment") or {}
    events = sorted(
        tracking.get("events") or [],
        key=lambda e: e.get("occurrenceDatetime") or e.get("datetime") or "",
        reverse=True,
    )

    couriers = [e.get("courierCode") for e in events if e.get("courierCode")]
    carrier = carrier_from_courier(couriers[0] if couriers else None) or carrier_hint or "unknown"
    result = TrackResult(
        tracking_number=number,
        carrier=carrier,
        source=Ship24.name,
        tracking_url=tracking_url(carrier, number),
    )
    milestone = shipment.get("statusMilestone")
    if not events and milestone in (None, "pending"):
        result.status = "not_found"
        result.note = "Ship24 has no courier data for this number yet; it may appear after the carrier's first scan."
        return result
    result.status = MILESTONES.get(milestone or "", "unknown")
    if result.status == "unknown" and events:
        result.status = MILESTONES.get(events[0].get("statusMilestone") or "", "in_transit")

    for ev in events:
        result.events.append(
            Event(
                time=ev.get("occurrenceDatetime") or ev.get("datetime"),
                description=(ev.get("status") or "").strip(),
                location=ev.get("location") or None,
                code=ev.get("statusCode"),
            )
        )
    result.status_text = result.events[0].description if result.events else milestone

    delivery = shipment.get("delivery") or {}
    if result.status == "delivered":
        result.delivered_at = result.events[0].time if result.events else None
    elif delivery.get("estimatedDeliveryDate"):
        stamp = delivery["estimatedDeliveryDate"]
        result.eta = Eta(date=stamp[:10], kind="estimated")
    return result
