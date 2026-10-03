"""Minimal MCP server over stdio (newline-delimited JSON-RPC 2.0).

Implements only what a tool server needs: initialize, ping, tools/list and
tools/call. Standard library only.
"""

from __future__ import annotations

import json
import sys
from datetime import timedelta
from typing import Any, Callable

from . import __version__, watch
from .detect import CARRIERS
from .router import Router

PROTOCOL_VERSION = "2025-06-18"

NUMBER = {"type": "string", "description": "Tracking number as printed (spaces and dashes are fine)."}
CARRIER = {
    "type": "string",
    "enum": list(CARRIERS),
    "description": "Only if the user said which carrier; otherwise it is detected from the number.",
}

TOOLS = [
    {
        "name": "track_package",
        "description": (
            "Look up a package now: status, expected delivery date or window, and recent scans, "
            "from the carrier's own API (FedEx, UPS), Amazon's public tracker, or Ship24 for others."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"tracking_number": NUMBER, "carrier": CARRIER},
            "required": ["tracking_number"],
        },
    },
    {
        "name": "watch_package",
        "description": "Add a package to the watch list (and look it up now) so check_watched reports changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tracking_number": NUMBER,
                "label": {"type": "string", "description": "What's in it, e.g. 'new monitor'."},
                "carrier": CARRIER,
            },
            "required": ["tracking_number"],
        },
    },
    {
        "name": "list_watched",
        "description": "Watched packages with status and expected delivery as of their last check (no network calls).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "unwatch_package",
        "description": "Remove a package from the watch list.",
        "inputSchema": {"type": "object", "properties": {"tracking_number": NUMBER}, "required": ["tracking_number"]},
    },
    {
        "name": "check_watched",
        "description": (
            "Refresh watched packages that are not delivered and report only what changed: status, "
            "delivery estimate, new scans. Packages checked in the last 15 minutes are skipped unless force is true."
        ),
        "inputSchema": {"type": "object", "properties": {"force": {"type": "boolean"}}},
    },
]


class Server:
    def __init__(self, router: Router):
        self.router = router
        self.handlers: dict[str, Callable[[dict], Any]] = {
            "track_package": self.track_package,
            "watch_package": self.watch_package,
            "list_watched": lambda a: watch.read(),
            "unwatch_package": lambda a: {"removed": watch.remove(a["tracking_number"])},
            "check_watched": lambda a: watch.check(
                self.router.track, min_age=timedelta(minutes=15), force=bool(a.get("force"))
            ),
        }

    def track_package(self, args: dict) -> dict:
        return self.router.track(args["tracking_number"], args.get("carrier")).to_dict(max_events=10)

    def watch_package(self, args: dict) -> dict:
        entry = watch.add(args["tracking_number"], args.get("label"), args.get("carrier"))
        result = self.router.track(args["tracking_number"], entry.get("carrier"))
        watch.record(args["tracking_number"], result)
        return {"watching": True, "label": entry.get("label"), "result": result.to_dict(max_events=5)}

    def handle(self, msg: dict) -> dict | None:
        method, msg_id = msg.get("method"), msg.get("id")
        if msg_id is None:
            return None  # notification
        try:
            result = self.dispatch(method, msg.get("params") or {})
        except LookupError:
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": f"Unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def dispatch(self, method: str, params: dict) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "parcel-watch", "version": __version__},
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call(params.get("name"), params.get("arguments") or {})
        raise LookupError(method)

    def call(self, name: str, args: dict) -> dict:
        handler = self.handlers.get(name)
        if handler is None:
            return _content({"error": f"unknown tool {name}"}, is_error=True)
        try:
            return _content(handler(args))
        except KeyError as e:
            return _content({"error": f"missing argument {e}"}, is_error=True)
        except Exception as e:  # report to the model instead of killing the server
            return _content({"error": f"{type(e).__name__}: {e}"}, is_error=True)


def _content(value: Any, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(value, indent=1)}], "isError": is_error}


def serve(router: Router, stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(router)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            reply = server.handle(msg) if isinstance(msg, dict) else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
