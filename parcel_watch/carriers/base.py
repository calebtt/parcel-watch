"""Shared provider plumbing."""

from __future__ import annotations

from typing import Optional

from ..config import Config, TokenCache
from ..detect import tracking_url
from ..model import TrackResult
from ..net import HttpError, Response


class ProviderError(Exception):
    """The source could not answer (auth failure, outage, quota)."""


class Provider:
    #: Short id used in results ("fedex-api") and for --source.
    name = ""
    #: Carriers this provider can answer for; "*" means any.
    carriers: tuple = ()
    #: Credentials it needs, for messages and `doctor`.
    required: tuple = ()

    def __init__(self, config: Config, tokens: Optional[TokenCache] = None):
        self.config = config
        self.tokens = tokens or TokenCache()

    def configured(self) -> bool:
        return self.config.has(*self.required)

    def missing(self) -> list[str]:
        return [k for k in self.required if not self.config.get(k)]

    def track(self, number: str) -> TrackResult:
        raise NotImplementedError

    def failure(self, carrier: str, number: str, message: str) -> TrackResult:
        return TrackResult(
            tracking_number=number,
            carrier=carrier,
            source=self.name,
            status="error",
            error=message,
            tracking_url=tracking_url(carrier, number),
        )


def describe_http_error(resp: Response, errors: Optional[list] = None) -> str:
    """'HTTP 403: CODE message' from a typical {"errors": [...]} body."""
    detail = ""
    for err in errors or []:
        if isinstance(err, dict):
            code = err.get("code") or ""
            message = err.get("message") or ""
            detail = f"{code} {message}".strip()
            break
    return f"HTTP {resp.status}" + (f": {detail}" if detail else f": {resp.text(200)}")


__all__ = ["Provider", "ProviderError", "HttpError", "describe_http_error"]
