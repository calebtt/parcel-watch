"""State directory, credentials and the OAuth token cache.

Credentials come from the environment first, then from `credentials.env` in
the state directory (KEY=VALUE lines). The state directory is
$PARCEL_WATCH_HOME, or ~/.config/parcel-watch.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

CREDENTIAL_KEYS = (
    "FEDEX_CLIENT_ID",
    "FEDEX_CLIENT_SECRET",
    "FEDEX_SANDBOX",
    "UPS_CLIENT_ID",
    "UPS_CLIENT_SECRET",
    "UPS_SANDBOX",
    "SHIP24_API_KEY",
    "PARCEL_WATCH_AMAZON",
)


def home() -> Path:
    env = os.environ.get("PARCEL_WATCH_HOME")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "parcel-watch"


def credentials_path() -> Path:
    return home() / "credentials.env"


def parse_env_file(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key] = value
    return values


class Config:
    def __init__(self, values: dict):
        self._values = values

    @classmethod
    def load(cls) -> "Config":
        values = {}
        path = credentials_path()
        if path.exists():
            mode = path.stat().st_mode
            if mode & (stat.S_IRWXG | stat.S_IRWXO):
                print(
                    f"warning: {path} is readable by other users; run chmod 600 on it",
                    file=sys.stderr,
                )
            values.update(parse_env_file(path.read_text()))
        for key in CREDENTIAL_KEYS:
            if os.environ.get(key):
                values[key] = os.environ[key]
        return cls(values)

    def get(self, key: str) -> Optional[str]:
        return self._values.get(key) or None

    def flag(self, key: str) -> bool:
        return (self.get(key) or "").lower() in ("1", "true", "yes", "on")

    def has(self, *keys: str) -> bool:
        return all(self.get(k) for k in keys)


def write_private(path: Path, text: str) -> None:
    """Atomically write a file readable only by the owner."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class TokenCache:
    """OAuth access tokens shared between runs, so each CLI call does not
    request a new token."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or home() / "tokens.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def get(self, name: str) -> Optional[str]:
        entry = self._read().get(name)
        if entry and entry.get("expires_at", 0) > time.time() + 60:
            return entry.get("token")
        return None

    def put(self, name: str, token: str, expires_in: float) -> None:
        data = self._read()
        data[name] = {"token": token, "expires_at": time.time() + float(expires_in)}
        write_private(self.path, json.dumps(data))

    def drop(self, name: str) -> None:
        data = self._read()
        if data.pop(name, None) is not None:
            write_private(self.path, json.dumps(data))
