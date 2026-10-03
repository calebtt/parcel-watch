"""Small JSON-over-HTTPS helper on top of urllib."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

from . import __version__

USER_AGENT = f"parcel-watch/{__version__} (+https://github.com/calebtt/parcel-watch)"
TIMEOUT = 30


class HttpError(Exception):
    """The request did not produce an HTTP response (DNS, TLS, timeout)."""


@dataclass
class Response:
    status: int
    body: bytes
    headers: dict

    def json(self) -> Any:
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def text(self, limit: int = 300) -> str:
        return self.body.decode("utf-8", "replace")[:limit]


def request(
    method: str,
    url: str,
    *,
    headers: Optional[dict] = None,
    json_body: Any = None,
    form: Optional[dict] = None,
    timeout: float = TIMEOUT,
) -> Response:
    data = None
    all_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        all_headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    all_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read() or b"", dict(e.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise HttpError(f"{urllib.parse.urlsplit(url).netloc}: {reason}") from e
