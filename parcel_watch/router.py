"""Pick a source for a tracking number and fall back when it can't answer.

Order: the carrier's own API for each candidate carrier, then Ship24.
"""

from __future__ import annotations

from typing import Optional

from .carriers.amazon import Amazon
from .carriers.base import HttpError, Provider, ProviderError
from .carriers.fedex import FedEx
from .carriers.ship24 import Ship24
from .carriers.ups import UPS
from .config import Config, TokenCache
from .detect import candidates, normalize, strip_zip_prefix, tracking_url
from .model import TrackResult

DIRECT = {"fedex": FedEx, "ups": UPS, "amazon": Amazon}

SETUP_HINT = "Run `parcel-watch doctor` to see which sources are set up."


class Router:
    def __init__(self, config: Optional[Config] = None, tokens: Optional[TokenCache] = None):
        self.config = config or Config.load()
        self.tokens = tokens or TokenCache()
        self.direct = {c: cls(self.config, self.tokens) for c, cls in DIRECT.items()}
        self.fallback = Ship24(self.config, self.tokens)

    def providers(self) -> list[Provider]:
        return [*self.direct.values(), self.fallback]

    def track(self, number: str, carrier: Optional[str] = None) -> TrackResult:
        n = normalize(number)
        order = [carrier] if carrier else candidates(n)
        tried: list[str] = []
        best: Optional[TrackResult] = None  # most useful non-answer so far

        for c in order:
            provider = self.direct.get(c)
            if provider is None:
                continue
            num = strip_zip_prefix(n) if c == "usps" else n
            result = self._ask(provider, c, num, tried)
            if result is None:
                continue
            if result.found:
                result.tried = tried[:-1]
                return result
            best = _prefer(best, result)

        hint = order[0] if order else None
        num = strip_zip_prefix(n) if hint == "usps" else n
        result = self._ask(self.fallback, hint or "unknown", num, tried, carrier_hint=hint)
        if result is not None:
            if result.found:
                result.tried = tried[:-1]
                return result
            best = _prefer(best, result)

        if best is None:
            guess = order[0] if order else "unknown"
            best = TrackResult(
                tracking_number=n,
                carrier=guess,
                source="none",
                status="error",
                tracking_url=tracking_url(guess, n),
                error=(
                    "No configured source can track this number"
                    + ("" if order else " (unrecognized format; pass a carrier)")
                    + ". "
                    + SETUP_HINT
                ),
            )
        best.tried = tried
        return best

    def _ask(self, provider: Provider, carrier: str, number: str, tried: list, **kwargs) -> Optional[TrackResult]:
        """None when the provider is not configured."""
        if not provider.configured():
            tried.append(f"{provider.name}: not configured (needs {', '.join(provider.missing())})")
            return None
        try:
            result = provider.track(number, **kwargs)
        except (ProviderError, HttpError) as e:
            tried.append(f"{provider.name}: {e}")
            return provider.failure(carrier, number, str(e))
        tried.append(f"{provider.name}: {result.status}")
        return result


def _prefer(current: Optional[TrackResult], new: TrackResult) -> TrackResult:
    """A real 'not found' beats a 'could not ask' error."""
    if current is None:
        return new
    if current.status == "error" and new.status == "not_found":
        return new
    return current
