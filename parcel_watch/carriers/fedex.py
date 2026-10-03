"""FedEx Track API (Basic Integrated Visibility) on developer.fedex.com.

This is FedEx's supported API. It is not behind the Akamai bot wall that
guards the fedex.com tracking page's own API.
"""

from __future__ import annotations

from typing import Optional

from .. import net
from ..detect import tracking_url
from ..model import Eta, Event, TrackResult, join_location
from .base import Provider, ProviderError, describe_http_error

PRODUCTION = "https://apis.fedex.com"
SANDBOX = "https://apis-sandbox.fedex.com"

NOT_FOUND_CODES = {"TRACKING.TRACKINGNUMBER.NOTFOUND", "TRACKING.DATA.NOTFOUND"}

# latestStatusDetail.derivedCode / code -> normalized status
STATUS_CODES = {
    "DL": "delivered",
    "OD": "out_for_delivery",
    "OC": "label_created",
    "IN": "label_created",
    "HL": "available_for_pickup",
    "HP": "available_for_pickup",
    "DE": "exception",
    "SE": "exception",
    "CA": "exception",
    "CD": "exception",
    "RS": "returned",
    "RP": "returned",
}


class FedEx(Provider):
    name = "fedex-api"
    carriers = ("fedex",)
    required = ("FEDEX_CLIENT_ID", "FEDEX_CLIENT_SECRET")

    @property
    def base(self) -> str:
        return SANDBOX if self.config.flag("FEDEX_SANDBOX") else PRODUCTION

    def _token(self, refresh: bool = False) -> str:
        key = f"fedex:{self.base}:{self.config.get('FEDEX_CLIENT_ID')}"
        if not refresh:
            cached = self.tokens.get(key)
            if cached:
                return cached
        resp = net.request(
            "POST",
            f"{self.base}/oauth/token",
            form={
                "grant_type": "client_credentials",
                "client_id": self.config.get("FEDEX_CLIENT_ID"),
                "client_secret": self.config.get("FEDEX_CLIENT_SECRET"),
            },
        )
        body = resp.json() or {}
        if resp.status != 200 or "access_token" not in body:
            raise ProviderError("FedEx sign-in failed, " + describe_http_error(resp, body.get("errors")))
        self.tokens.put(key, body["access_token"], body.get("expires_in", 3600))
        return body["access_token"]

    def track(self, number: str) -> TrackResult:
        payload = {
            "includeDetailedScans": True,
            "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": number}}],
        }
        resp = None
        for attempt in range(2):
            resp = net.request(
                "POST",
                f"{self.base}/track/v1/trackingnumbers",
                headers={"Authorization": f"Bearer {self._token(refresh=attempt > 0)}", "X-locale": "en_US"},
                json_body=payload,
            )
            if resp.status != 401:
                break
        body = resp.json() or {}
        if resp.status != 200:
            errors = body.get("errors") or []
            if any((e or {}).get("code") in NOT_FOUND_CODES for e in errors):
                return parse(number, {})
            raise ProviderError("FedEx tracking failed, " + describe_http_error(resp, errors))
        return parse(number, body)


def parse(number: str, body: dict) -> TrackResult:
    result = TrackResult(
        tracking_number=number,
        carrier="fedex",
        source=FedEx.name,
        tracking_url=tracking_url("fedex", number),
    )
    complete = ((body.get("output") or {}).get("completeTrackResults") or [{}])[0]
    track_results = complete.get("trackResults") or []
    usable = [r for r in track_results if not r.get("error")]
    if not usable:
        error = (track_results[0].get("error") if track_results else None) or {}
        if not track_results or error.get("code") in NOT_FOUND_CODES:
            result.status = "not_found"
            result.note = "FedEx has no record of this number yet."
        else:
            result.status = "error"
            result.error = f"{error.get('code')}: {error.get('message')}"
        return result
    if len(usable) > 1:
        result.note = (
            f"FedEx matched {len(usable)} shipments with this number; showing the first. "
            "Check the tracking page if this is not the right package."
        )
    r = usable[0]

    latest = r.get("latestStatusDetail") or {}
    code = latest.get("derivedCode") or latest.get("code") or ""
    result.status = STATUS_CODES.get(code, "in_transit" if code else "unknown")
    result.status_text = latest.get("statusByLocale") or latest.get("description")
    result.delayed = (latest.get("delayDetail") or {}).get("status") == "DELAYED"

    dates = {d.get("type"): d.get("dateTime") for d in r.get("dateAndTimes") or [] if d.get("dateTime")}
    result.delivered_at = dates.get("ACTUAL_DELIVERY") if result.status == "delivered" else None
    if result.status != "delivered":
        result.eta = _eta(r, dates)
        commit = r.get("serviceCommitMessage") or {}
        if result.eta is None and commit.get("message"):
            result.note = commit["message"]

    for scan in r.get("scanEvents") or []:
        loc = scan.get("scanLocation") or {}
        result.events.append(
            Event(
                time=scan.get("date"),
                description=scan.get("eventDescription") or scan.get("derivedStatus") or "",
                location=join_location(loc.get("city"), loc.get("stateOrProvinceCode"), loc.get("countryCode")),
                code=scan.get("eventType"),
            )
        )
    return result


def _eta(r: dict, dates: dict) -> Optional[Eta]:
    window = (r.get("estimatedDeliveryTimeWindow") or {}).get("window") or {}
    if window.get("begins") or window.get("ends"):
        start, end = window.get("begins"), window.get("ends")
        return Eta(date=(start or end)[:10], start=start, end=end, kind="estimated")
    for kind, key in (("estimated", "ESTIMATED_DELIVERY"), ("appointment", "APPOINTMENT_DELIVERY"), ("commitment", "COMMITMENT")):
        value = dates.get(key)
        if value:
            # A midnight time means FedEx gave a date only.
            has_time = len(value) > 10 and value[11:19] not in ("00:00:00", "")
            return Eta(date=value[:10], end=value if has_time else None, kind=kind)
    standard = (r.get("standardTransitTimeWindow") or {}).get("window") or {}
    if standard.get("ends"):
        return Eta(date=standard["ends"][:10], kind="standard_transit")
    return None
