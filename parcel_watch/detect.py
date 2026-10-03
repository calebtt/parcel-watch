"""Guess the carrier from a tracking number's format.

Formats overlap (a 22-digit number starting with 92 can be USPS or FedEx
Ground Economy), so `candidates` returns carriers in the order to try.
"""

from __future__ import annotations

import re

CARRIERS = ("fedex", "ups", "usps", "amazon", "dhl")

TRACKING_URLS = {
    "fedex": "https://www.fedex.com/fedextrack/?trknbr={}",
    "ups": "https://www.ups.com/track?tracknum={}",
    "usps": "https://tools.usps.com/go/TrackConfirmAction?tLabels={}",
    "amazon": "https://track.amazon.com/tracking/{}",
    "dhl": "https://www.dhl.com/us-en/home/tracking.html?tracking-id={}",
}


def normalize(number: str) -> str:
    """Strip spaces, dashes and dots, and upper-case."""
    return re.sub(r"[\s.\-]", "", number).upper()


def candidates(number: str) -> list[str]:
    n = normalize(number)
    if re.fullmatch(r"1Z[0-9A-Z]{16}", n):
        return ["ups"]
    if re.fullmatch(r"TB[A-CM]\d{12}", n):
        return ["amazon"]
    if re.fullmatch(r"[A-Z]{2}\d{9}US", n):
        return ["usps"]
    if not n.isdigit():
        return []
    length = len(n)
    if length in (12, 15, 34):
        return ["fedex"]
    if length == 22 and n.startswith("96"):
        return ["fedex"]
    if length in (20, 22) and n.startswith("9"):
        return ["usps", "fedex"]
    if length in (26, 30) and n.startswith("420"):
        return ["usps"]
    if length == 10:
        return ["dhl"]
    return []


def strip_zip_prefix(number: str) -> str:
    """USPS label barcodes can carry a 420+ZIP routing prefix."""
    n = normalize(number)
    if n.startswith("420") and len(n) in (26, 30):
        return n[len(n) - 22:]
    return n


def tracking_url(carrier: str, number: str) -> str | None:
    pattern = TRACKING_URLS.get(carrier)
    return pattern.format(number) if pattern else None
