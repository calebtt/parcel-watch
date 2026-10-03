"""UPS Tracking API on developer.ups.com (OAuth client credentials)."""

from __future__ import annotations

import base64
import uuid
from typing import Optional

from .. import net
from ..detect import tracking_url
from ..model import Eta, Event, TrackResult, join_location
from .base import Provider, ProviderError, describe_http_error

PRODUCTION = "https://onlinetools.ups.com"
SANDBOX = "https://wwwcie.ups.com"

NOT_FOUND_CODES = {"TW0001"}

# currentStatus.type -> normalized status
STATUS_TYPES = {
    "D": "delivered",
    "O": "out_for_delivery",
    "I": "in_transit",
    "P": "in_transit",
    "M": "label_created",
    "MV": "exception",
    "X": "exception",
    "RS": "returned",
}


class UPS(Provider):
    name = "ups-api"
    carriers = ("ups",)
    required = ("UPS_CLIENT_ID", "UPS_CLIENT_SECRET")

    @property
    def base(self) -> str:
        return SANDBOX if self.config.flag("UPS_SANDBOX") else PRODUCTION

    def _token(self, refresh: bool = False) -> str:
        key = f"ups:{self.base}:{self.config.get('UPS_CLIENT_ID')}"
        if not refresh:
            cached = self.tokens.get(key)
            if cached:
                return cached
        pair = f"{self.config.get('UPS_CLIENT_ID')}:{self.config.get('UPS_CLIENT_SECRET')}"
        resp = net.request(
            "POST",
            f"{self.base}/security/v1/oauth/token",
            headers={"Authorization": "Basic " + base64.b64encode(pair.encode()).decode()},
            form={"grant_type": "client_credentials"},
        )
        body = resp.json() or {}
        if resp.status != 200 or "access_token" not in body:
            errors = (body.get("response") or {}).get("errors")
            raise ProviderError("UPS sign-in failed, " + describe_http_error(resp, errors))
        self.tokens.put(key, body["access_token"], float(body.get("expires_in") or 3600))
        return body["access_token"]

    def track(self, number: str) -> TrackResult:
        resp = None
        for attempt in range(2):
            resp = net.request(
                "GET",
                f"{self.base}/api/track/v1/details/{number}"
                "?locale=en_US&returnSignature=false&returnMilestones=false&returnPOD=false",
                headers={
                    "Authorization": f"Bearer {self._token(refresh=attempt > 0)}",
                    "transId": uuid.uuid4().hex,
                    "transactionSrc": "parcel-watch",
                },
            )
            if resp.status != 401:
                break
        body = resp.json() or {}
        errors = (body.get("response") or {}).get("errors") or []
        if resp.status == 404 or any((e or {}).get("code") in NOT_FOUND_CODES for e in errors):
            return parse(number, {})
        if resp.status != 200:
            raise ProviderError("UPS tracking failed, " + describe_http_error(resp, errors))
        return parse(number, body)


def parse(number: str, body: dict) -> TrackResult:
    result = TrackResult(
        tracking_number=number,
        carrier="ups",
        source=UPS.name,
        tracking_url=tracking_url("ups", number),
    )
    shipment = ((body.get("trackResponse") or {}).get("shipment") or [{}])[0]
    packages = shipment.get("package") or []
    if not packages:
        warning = (shipment.get("warnings") or [{}])[0]
        result.status = "not_found"
        result.note = warning.get("message") or "UPS has no record of this number yet."
        return result
    pkg = next((p for p in packages if p.get("trackingNumber") == number), packages[0])

    current = pkg.get("currentStatus") or {}
    status_type = (current.get("type") or "").upper()
    text = current.get("description") or current.get("simplifiedTextDescription") or ""
    lowered = text.lower()
    if "out for delivery" in lowered:
        result.status = "out_for_delivery"
    elif "pickup" in lowered and ("ready" in lowered or "available" in lowered):
        result.status = "available_for_pickup"
    else:
        result.status = STATUS_TYPES.get(status_type, "in_transit" if status_type else "unknown")
    result.status_text = text.strip() or None

    for act in pkg.get("activity") or []:
        status = act.get("status") or {}
        addr = (act.get("location") or {}).get("address") or {}
        stamp = _stamp(act.get("date"), act.get("time"))
        if stamp and act.get("gmtOffset"):
            stamp += act["gmtOffset"]
        result.events.append(
            Event(
                time=stamp,
                description=(status.get("description") or "").strip(),
                location=join_location(addr.get("city"), addr.get("stateProvince"), addr.get("countryCode")),
                code=status.get("statusCode") or status.get("code"),
            )
        )

    dates = {d.get("type"): d.get("date") for d in pkg.get("deliveryDate") or [] if d.get("date")}
    time = pkg.get("deliveryTime") or {}
    if result.status == "delivered":
        delivered_time = time.get("endTime") if time.get("type") == "DEL" else None
        result.delivered_at = _stamp(dates.get("DEL"), delivered_time) or (result.events[0].time if result.events else None)
    else:
        result.eta = _eta(dates, time)
        result.delayed = "RDD" in dates
    return result


def _date(yyyymmdd: Optional[str]) -> Optional[str]:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


def _stamp(yyyymmdd: Optional[str], hhmmss: Optional[str]) -> Optional[str]:
    """Local date and time; UPS does not give an offset for these."""
    day = _date(yyyymmdd)
    if not day:
        return None
    if not hhmmss or len(hhmmss) != 6:
        return day
    return f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}"


def _eta(dates: dict, time: dict) -> Optional[Eta]:
    if "RDD" in dates:
        day, kind = dates["RDD"], "rescheduled"
    elif "SDD" in dates:
        day, kind = dates["SDD"], "scheduled"
    else:
        return None
    eta = Eta(date=_date(day), kind=kind)
    time_type = time.get("type")
    if time_type in ("EDW", "CDW", "IDW"):
        eta.start = _stamp(day, time.get("startTime"))
        eta.end = _stamp(day, time.get("endTime"))
    elif time_type == "CMT":
        eta.end = _stamp(day, time.get("endTime"))
        eta.kind = "commitment"
    return eta
