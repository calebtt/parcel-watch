"""HTTP flows with net.request mocked: tokens, retries, routing, fallbacks."""

import json
import os
import stat
import unittest
from unittest import mock

from parcel_watch import config, net
from parcel_watch.config import Config, TokenCache
from parcel_watch.detect import candidates, normalize, strip_zip_prefix
from parcel_watch.router import Router
from tests.helpers import StateDirTest, fixture


def resp(status, body):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return net.Response(status, raw, {})


class FakeNet:
    """Answers requests from a list of (predicate, response) rules and logs calls."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for i, (match, response) in enumerate(self.rules):
            if match in url:
                if isinstance(response, list):
                    return response.pop(0) if len(response) > 1 else response[0]
                return response
        raise AssertionError(f"unexpected request {method} {url}")

    def urls(self):
        return [u for _, u, _ in self.calls]


class Detect(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(candidates("1Z 999 AA1 01 2345 6784"), ["ups"])
        self.assertEqual(candidates("tba123456789000"), ["amazon"])
        self.assertEqual(candidates("1234 5678 9012"), ["fedex"])
        self.assertEqual(candidates("123456789012345"), ["fedex"])
        self.assertEqual(candidates("9612019" + "0" * 15), ["fedex"])
        self.assertEqual(candidates("9400111899223197428490"), ["usps", "fedex"])
        self.assertEqual(candidates("EA123456789US"), ["usps"])
        self.assertEqual(candidates("1234567890"), ["dhl"])
        self.assertEqual(candidates("ABC"), [])

    def test_zip_prefix(self):
        self.assertEqual(strip_zip_prefix("42030301" + "9400111899223197428490"), "9400111899223197428490")
        self.assertEqual(normalize(" 1z-999.aa1 "), "1Z999AA1")


class ConfigTests(StateDirTest):
    def test_env_file_and_env_override(self):
        path = config.credentials_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# comment\nFEDEX_CLIENT_ID=from-file\nexport UPS_CLIENT_ID='quoted'\n")
        path.chmod(0o600)
        os.environ["FEDEX_CLIENT_ID"] = "from-env"
        cfg = Config.load()
        self.assertEqual(cfg.get("FEDEX_CLIENT_ID"), "from-env")
        self.assertEqual(cfg.get("UPS_CLIENT_ID"), "quoted")

    def test_token_cache_is_private_and_expires(self):
        cache = TokenCache()
        cache.put("a", "tok", 3600)
        self.assertEqual(cache.get("a"), "tok")
        self.assertEqual(stat.S_IMODE(cache.path.stat().st_mode), 0o600)
        cache.put("b", "short", 30)  # inside the 60 s safety margin
        self.assertIsNone(cache.get("b"))
        cache.drop("a")
        self.assertIsNone(cache.get("a"))


class FedExFlow(StateDirTest):
    def router(self, **extra):
        return Router(Config({"FEDEX_CLIENT_ID": "id", "FEDEX_CLIENT_SECRET": "secret", **extra}))

    def test_token_then_track_and_token_reuse(self):
        fake = FakeNet(
            ("/oauth/token", resp(200, {"access_token": "T1", "expires_in": 3599})),
            ("/track/v1/trackingnumbers", resp(200, fixture("fedex-in-transit.json"))),
        )
        with mock.patch.object(net, "request", fake):
            r = self.router().track("1111 1111 1111")
            self.router().track("111111111111")
        self.assertEqual(r.source, "fedex-api")
        self.assertEqual(r.eta.start, "2026-10-06T10:00:00-04:00")
        self.assertEqual(r.tried, [])
        token_calls = [c for c in fake.calls if c[1].endswith("/oauth/token")]
        self.assertEqual(len(token_calls), 1, "second run should reuse the cached token")
        self.assertEqual(token_calls[0][2]["form"]["grant_type"], "client_credentials")
        method, url, kw = fake.calls[1]
        self.assertEqual(url, "https://apis.fedex.com/track/v1/trackingnumbers")
        self.assertEqual(kw["headers"]["Authorization"], "Bearer T1")
        self.assertEqual(kw["json_body"]["trackingInfo"][0]["trackingNumberInfo"]["trackingNumber"], "111111111111")

    def test_expired_token_is_refreshed_once(self):
        fake = FakeNet(
            ("/oauth/token", [resp(200, {"access_token": "OLD", "expires_in": 3599}), resp(200, {"access_token": "NEW", "expires_in": 3599})]),
            ("/track/v1/trackingnumbers", [resp(401, {"errors": [{"code": "NOT.AUTHORIZED.ERROR"}]}), resp(200, fixture("fedex-delivered.json"))]),
        )
        with mock.patch.object(net, "request", fake):
            r = self.router().track("111111111111")
        self.assertEqual(r.status, "delivered")
        auths = [kw["headers"]["Authorization"] for _, u, kw in fake.calls if "trackingnumbers" in u]
        self.assertEqual(auths, ["Bearer OLD", "Bearer NEW"])

    def test_sandbox_flag(self):
        fake = FakeNet(
            ("/oauth/token", resp(200, {"access_token": "T", "expires_in": 3599})),
            ("/track/", resp(200, fixture("fedex-in-transit.json"))),
        )
        with mock.patch.object(net, "request", fake):
            self.router(FEDEX_SANDBOX="1").track("111111111111")
        self.assertTrue(all(u.startswith("https://apis-sandbox.fedex.com/") for u in fake.urls()))

    def test_bad_credentials_are_an_error_not_a_crash(self):
        fake = FakeNet(("/oauth/token", resp(401, {"errors": [{"code": "NOT.AUTHORIZED.ERROR", "message": "bad creds"}]})))
        with mock.patch.object(net, "request", fake):
            r = self.router().track("111111111111")
        self.assertEqual(r.status, "error")
        self.assertIn("FedEx sign-in failed, HTTP 401: NOT.AUTHORIZED.ERROR bad creds", r.error)
        self.assertEqual(r.tried[-1], "ship24: not configured (needs SHIP24_API_KEY)")

    def test_usps_shaped_number_tries_fedex_then_ship24(self):
        fake = FakeNet(
            ("/oauth/token", resp(200, {"access_token": "T", "expires_in": 3599})),
            ("/track/v1/trackingnumbers", resp(404, {"errors": [{"code": "TRACKING.TRACKINGNUMBER.NOTFOUND"}]})),
            ("api.ship24.com", resp(201, fixture("ship24-usps.json"))),
        )
        with mock.patch.object(net, "request", fake):
            r = self.router(SHIP24_API_KEY="k").track("9400100000000000000000")
        self.assertEqual(r.source, "ship24")
        self.assertEqual(r.carrier, "usps")
        self.assertEqual(r.tried, ["fedex-api: not_found"])
        ship24_call = fake.calls[-1]
        self.assertEqual(ship24_call[2]["headers"]["Authorization"], "Bearer k")
        self.assertEqual(ship24_call[2]["json_body"], {"trackingNumber": "9400100000000000000000"})


class UPSFlow(StateDirTest):
    def test_basic_auth_and_headers(self):
        fake = FakeNet(
            ("/security/v1/oauth/token", resp(200, {"access_token": "U", "expires_in": "14399"})),
            ("/api/track/v1/details/", resp(200, fixture("ups-in-transit.json"))),
        )
        cfg = Config({"UPS_CLIENT_ID": "id", "UPS_CLIENT_SECRET": "secret"})
        with mock.patch.object(net, "request", fake):
            r = Router(cfg).track("1Z0000000000000000")
        self.assertEqual(r.status, "in_transit")
        token_kw = fake.calls[0][2]
        self.assertEqual(token_kw["headers"]["Authorization"], "Basic aWQ6c2VjcmV0")
        self.assertEqual(token_kw["form"], {"grant_type": "client_credentials"})
        _, url, kw = fake.calls[1]
        self.assertTrue(url.startswith("https://onlinetools.ups.com/api/track/v1/details/1Z0000000000000000?"))
        self.assertEqual(kw["headers"]["transactionSrc"], "parcel-watch")
        self.assertLessEqual(len(kw["headers"]["transId"]), 32)

    def test_404_is_not_found(self):
        fake = FakeNet(
            ("/security/v1/oauth/token", resp(200, {"access_token": "U", "expires_in": "14399"})),
            ("/api/track/v1/details/", resp(404, {"response": {"errors": [{"code": "TW0001", "message": "Tracking Information Not Found"}]}})),
        )
        cfg = Config({"UPS_CLIENT_ID": "id", "UPS_CLIENT_SECRET": "secret"})
        with mock.patch.object(net, "request", fake):
            r = Router(cfg).track("1Z0000000000000000")
        self.assertEqual(r.status, "not_found")


class Routing(StateDirTest):
    def test_amazon_needs_no_credentials_and_can_be_turned_off(self):
        fake = FakeNet(("track.amazon.com/api/tracker/TBA000000000000", resp(200, fixture("amazon-in-transit.json"))))
        with mock.patch.object(net, "request", fake):
            self.assertEqual(Router(Config({})).track("TBA000000000000").status, "in_transit")
            off = Router(Config({"PARCEL_WATCH_AMAZON": "off"})).track("TBA000000000000")
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(off.status, "error")
        self.assertEqual(off.source, "none")

    def test_nothing_configured_names_the_likely_carrier(self):
        with mock.patch.object(net, "request", FakeNet()):
            r = Router(Config({})).track("9400100000000000000000")
        self.assertEqual(r.carrier, "usps")
        self.assertEqual(r.status, "error")
        self.assertIn("doctor", r.error)
        self.assertEqual(
            r.tried,
            ["fedex-api: not configured (needs FEDEX_CLIENT_ID, FEDEX_CLIENT_SECRET)", "ship24: not configured (needs SHIP24_API_KEY)"],
        )

    def test_not_found_beats_a_later_outage(self):
        fake = FakeNet(
            ("track.amazon.com", resp(200, fixture("amazon-not-found.json"))),
            ("api.ship24.com", resp(503, b"Service Unavailable")),
        )
        with mock.patch.object(net, "request", fake):
            r = Router(Config({"SHIP24_API_KEY": "k"})).track("TBA123456789000")
        self.assertEqual(r.status, "not_found")
        self.assertEqual(r.source, "amazon-public")
        self.assertEqual(r.tried[0], "amazon-public: not_found")
        self.assertTrue(r.tried[1].startswith("ship24: Ship24 tracking failed, HTTP 503"))

    def test_network_failure_is_reported(self):
        def boom(*a, **k):
            raise net.HttpError("track.amazon.com: timed out")

        with mock.patch.object(net, "request", boom):
            r = Router(Config({})).track("TBA000000000000")
        self.assertEqual(r.status, "error")
        self.assertIn("timed out", r.error)


if __name__ == "__main__":
    unittest.main()
