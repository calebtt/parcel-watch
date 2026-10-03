import io
import json
from datetime import datetime, timedelta, timezone
from unittest import mock

from parcel_watch import cli, mcp, watch
from parcel_watch.model import Eta, Event, TrackResult
from tests.helpers import StateDirTest


def result(n="111111111111", status="in_transit", eta_date="2026-10-06", event="Departed FedEx hub", **kw):
    return TrackResult(
        tracking_number=n,
        carrier="fedex",
        source="fedex-api",
        status=status,
        eta=Eta(date=eta_date) if eta_date else None,
        events=[Event(time="2026-10-03T08:12:00-05:00", description=event, location="MEMPHIS, TN, US")],
        **kw,
    )


class FakeRouter:
    def __init__(self, *results):
        self.queue = list(results)
        self.calls = []

    def track(self, number, carrier=None):
        self.calls.append((number, carrier))
        return self.queue.pop(0)

    def providers(self):
        return []


class WatchList(StateDirTest):
    def test_add_record_and_diff(self):
        watch.add("1111 1111 1111", "monitor", None)
        self.assertEqual(watch.record("111111111111", result()), ["now watching"])
        self.assertEqual(watch.record("111111111111", result()), [])
        changes = watch.record("111111111111", result(status="out_for_delivery", eta_date="2026-10-05", event="On FedEx vehicle for delivery"))
        self.assertEqual(
            changes,
            [
                "status in_transit -> out_for_delivery",
                "delivery estimate 2026-10-06 -> 2026-10-05",
                "new scan: On FedEx vehicle for delivery (MEMPHIS, TN, US)",
            ],
        )
        entry = watch.read()["111111111111"]
        self.assertEqual(entry["label"], "monitor")
        self.assertEqual(entry["snapshot"]["status"], "out_for_delivery")

    def test_errors_keep_the_last_good_snapshot(self):
        watch.add("111111111111", None, None)
        watch.record("111111111111", result())
        err = TrackResult(tracking_number="111111111111", carrier="fedex", source="fedex-api", status="error", error="HTTP 503")
        self.assertEqual(watch.record("111111111111", err), [])
        entry = watch.read()["111111111111"]
        self.assertEqual(entry["snapshot"]["status"], "in_transit")
        self.assertEqual(entry["last_error"], "HTTP 503")
        self.assertEqual(watch.record("111111111111", result()), [])
        self.assertNotIn("last_error", watch.read()["111111111111"])

    def test_check_skips_recent_and_delivered_and_prunes_old(self):
        watch.add("111111111111", "a", None)
        watch.add("222222222222", "b", "fedex")
        watch.add("333333333333", "c", None)
        watch.record("111111111111", result())  # just checked
        watch.record("333333333333", result(n="333333333333", status="delivered", eta_date=None, delivered_at="2026-09-20T10:00:00-04:00"))
        with watch.locked() as data:
            old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
            data["packages"]["333333333333"]["delivered_seen_at"] = old

        router = FakeRouter(result(n="222222222222", status="delivered", eta_date=None, event="Delivered"))
        report = watch.check(router.track)
        self.assertEqual(router.calls, [("222222222222", "fedex")])
        self.assertEqual(report["skipped"], 1)
        self.assertEqual(report["pruned"], ["333333333333"])
        self.assertEqual(report["changed"][0]["tracking_number"], "222222222222")
        self.assertEqual(report["changed"][0]["changes"][0], "now watching")

        # Delivered packages are not checked again; force re-checks the recent one.
        router = FakeRouter(result())
        report = watch.check(router.track, force=True)
        self.assertEqual(router.calls, [("111111111111", None)])
        self.assertEqual(report["changed"], [])

    def test_remove(self):
        watch.add("111111111111", None, None)
        self.assertTrue(watch.remove("1111-1111-1111"))
        self.assertFalse(watch.remove("111111111111"))

    def test_watchlist_is_private(self):
        watch.add("111111111111", None, None)
        self.assertEqual(watch.path().stat().st_mode & 0o077, 0)


class CLI(StateDirTest):
    def run_cli(self, argv, router):
        out = io.StringIO()
        with mock.patch.object(cli, "Router", lambda: router), mock.patch("sys.stdout", out):
            code = cli.main(argv)
        return code, out.getvalue()

    def test_track_text(self):
        r = result(note="FedEx note")
        r.eta = Eta(date="2026-10-06", start="2026-10-06T10:00:00-04:00", end="2026-10-06T14:00:00-04:00")
        code, out = self.run_cli(["track", "111111111111"], FakeRouter(r))
        self.assertEqual(code, 0)
        self.assertIn("FedEx 111111111111: In transit", out)
        self.assertIn("Expected: Tue Oct 6, between 10:00 AM and 2:00 PM (estimated)", out)
        self.assertIn("Sat Oct 3, 8:12 AM: Departed FedEx hub - MEMPHIS, TN, US", out)

    def test_track_json_and_error_exit_code(self):
        err = TrackResult(tracking_number="X", carrier="unknown", source="none", status="error", error="nope")
        code, out = self.run_cli(["track", "X", "--json"], FakeRouter(err))
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["error"], "nope")

    def test_add_list_check_quiet(self):
        router = FakeRouter(result(), result())
        self.run_cli(["add", "111111111111", "--label", "monitor"], router)
        _, out = self.run_cli(["list"], router)
        self.assertIn("FedEx 111111111111 (monitor): In transit, expected 2026-10-06", out)
        _, out = self.run_cli(["check", "--quiet", "--force"], router)
        self.assertEqual(out, "")

    def test_check_reports_changes(self):
        router = FakeRouter(result(), result(eta_date="2026-10-08"))
        self.run_cli(["add", "111111111111"], router)
        _, out = self.run_cli(["check", "--force"], router)
        self.assertIn("delivery estimate 2026-10-06 -> 2026-10-08", out)


class MCPServer(StateDirTest):
    def session(self, router, *messages):
        stdin = io.StringIO("\n".join(json.dumps(m) for m in messages) + "\n")
        stdout = io.StringIO()
        mcp.serve(router, stdin, stdout)
        return [json.loads(line) for line in stdout.getvalue().splitlines()]

    def test_handshake_and_tools(self):
        replies = self.session(
            FakeRouter(result()),
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "watch_package", "arguments": {"tracking_number": "111111111111", "label": "monitor"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "list_watched", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "nope"},
        )
        self.assertEqual([r["id"] for r in replies], [1, 2, 3, 4, 5])
        self.assertEqual(replies[0]["result"]["protocolVersion"], "2025-03-26")
        names = [t["name"] for t in replies[1]["result"]["tools"]]
        self.assertEqual(names, ["track_package", "watch_package", "list_watched", "unwatch_package", "check_watched"])
        watched = json.loads(replies[2]["result"]["content"][0]["text"])
        self.assertTrue(watched["watching"])
        self.assertEqual(watched["result"]["eta"]["date"], "2026-10-06")
        listing = json.loads(replies[3]["result"]["content"][0]["text"])
        self.assertEqual(listing["111111111111"]["label"], "monitor")
        self.assertEqual(replies[4]["error"]["code"], -32601)

    def test_tool_errors_are_reported_not_raised(self):
        replies = self.session(
            FakeRouter(),
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "track_package", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "track_package", "arguments": {"tracking_number": "1"}}},
        )
        self.assertTrue(replies[0]["result"]["isError"])
        self.assertIn("missing argument", replies[0]["result"]["content"][0]["text"])
        self.assertTrue(replies[1]["result"]["isError"])  # FakeRouter queue is empty -> IndexError
