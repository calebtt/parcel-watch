import json
import unittest

from parcel_watch.carriers import amazon, fedex, ship24, ups
from tests.helpers import assert_no_private, fixture

FX = "111111111111"
UPS_N = "1Z0000000000000000"


class FedExParse(unittest.TestCase):
    def test_in_transit_uses_the_delivery_window(self):
        r = fedex.parse(FX, fixture("fedex-in-transit.json"))
        self.assertEqual(r.status, "in_transit")
        self.assertEqual(r.status_text, "In transit")
        self.assertEqual(r.eta.date, "2026-10-06")
        self.assertEqual(r.eta.start, "2026-10-06T10:00:00-04:00")
        self.assertEqual(r.eta.end, "2026-10-06T14:00:00-04:00")
        self.assertEqual(r.eta.kind, "estimated")
        self.assertEqual(len(r.events), 3)
        self.assertEqual(r.events[0].description, "Departed FedEx hub")
        self.assertEqual(r.events[0].location, "MEMPHIS, TN, US")
        self.assertEqual(r.tracking_url, "https://www.fedex.com/fedextrack/?trknbr=111111111111")
        assert_no_private(self, r)

    def test_date_only_estimate_and_delay(self):
        r = fedex.parse(FX, fixture("fedex-date-only.json"))
        self.assertEqual(r.status, "in_transit")
        self.assertEqual(r.eta.date, "2026-10-08")
        self.assertIsNone(r.eta.start)
        self.assertIsNone(r.eta.end)
        self.assertTrue(r.delayed)

    def test_no_estimate_passes_on_fedex_message(self):
        r = fedex.parse(FX, fixture("fedex-no-estimate.json"))
        self.assertEqual(r.status, "label_created")
        self.assertIsNone(r.eta)
        self.assertEqual(r.note, "No scheduled delivery date available at this time.")

    def test_delivered(self):
        r = fedex.parse(FX, fixture("fedex-delivered.json"))
        self.assertEqual(r.status, "delivered")
        self.assertEqual(r.delivered_at, "2026-10-06T11:32:00-04:00")
        self.assertIsNone(r.eta)
        assert_no_private(self, r)

    def test_not_found(self):
        r = fedex.parse(FX, fixture("fedex-not-found.json"))
        self.assertEqual(r.status, "not_found")
        self.assertFalse(r.found)

    def test_empty_body_is_not_found(self):
        self.assertEqual(fedex.parse(FX, {}).status, "not_found")


class UPSParse(unittest.TestCase):
    def test_in_transit_with_window(self):
        r = ups.parse(UPS_N, fixture("ups-in-transit.json"))
        self.assertEqual(r.status, "in_transit")
        self.assertEqual(r.status_text, "On the Way")
        self.assertEqual(r.eta.date, "2026-10-06")
        self.assertEqual(r.eta.start, "2026-10-06T10:30:00")
        self.assertEqual(r.eta.end, "2026-10-06T14:30:00")
        self.assertEqual(r.eta.kind, "scheduled")
        self.assertFalse(r.delayed)
        self.assertEqual(r.events[0].time, "2026-10-03T08:12:00-05:00")
        self.assertEqual(r.events[0].description, "Departed from Facility")
        self.assertEqual(r.events[0].location, "Louisville, KY, US")
        self.assertEqual(r.events[1].time, "2026-10-02T19:00:00")
        assert_no_private(self, r)

    def test_rescheduled_date_wins(self):
        r = ups.parse(UPS_N, fixture("ups-rescheduled.json"))
        self.assertEqual(r.status, "exception")
        self.assertEqual(r.eta.date, "2026-10-07")
        self.assertEqual(r.eta.kind, "rescheduled")
        self.assertTrue(r.delayed)

    def test_out_for_delivery_with_commit_time(self):
        r = ups.parse(UPS_N, fixture("ups-out-for-delivery.json"))
        self.assertEqual(r.status, "out_for_delivery")
        self.assertEqual(r.eta.end, "2026-10-06T10:30:00")
        self.assertEqual(r.eta.kind, "commitment")

    def test_delivered(self):
        r = ups.parse(UPS_N, fixture("ups-delivered.json"))
        self.assertEqual(r.status, "delivered")
        self.assertEqual(r.delivered_at, "2026-10-06T11:32:00")
        self.assertIsNone(r.eta)
        assert_no_private(self, r)

    def test_delivered_time_falls_back_to_the_last_scan(self):
        body = fixture("ups-delivered.json")
        pkg = body["trackResponse"]["shipment"][0]["package"][0]
        pkg["deliveryDate"], pkg["deliveryTime"] = [], {}
        self.assertEqual(ups.parse(UPS_N, body).delivered_at, "2026-10-06T11:32:00")

    def test_not_found(self):
        r = ups.parse(UPS_N, fixture("ups-not-found.json"))
        self.assertEqual(r.status, "not_found")
        self.assertEqual(r.note, "Tracking Information Not Found")


class AmazonParse(unittest.TestCase):
    def test_in_transit(self):
        r = amazon.parse("TBA000000000000", fixture("amazon-in-transit.json"))
        self.assertEqual(r.status, "in_transit")
        self.assertEqual(r.eta.date, "2026-10-05")
        self.assertEqual(r.eta.end, "2026-10-05T22:00:00")
        # newest first, verbatim duplicate dropped
        self.assertEqual([e.code for e in r.events], ["Departed", "CreationConfirmed"])
        self.assertEqual(r.events[0].time, "2026-10-03T06:40:11")
        self.assertEqual(r.events[0].location, "Fort Worth, TX, US")
        assert_no_private(self, r)

    def test_delivered(self):
        r = amazon.parse("TBA000000000000", fixture("amazon-delivered.json"))
        self.assertEqual(r.status, "delivered")
        self.assertEqual(r.delivered_at, "2026-10-05T14:03:44")
        self.assertIsNone(r.eta)

    def test_real_not_found_reply(self):
        r = amazon.parse("TBA123456789000", fixture("amazon-not-found.json"))
        self.assertEqual(r.status, "not_found")
        self.assertIn("Your Orders", r.note)

    def test_time_formats(self):
        self.assertEqual(amazon.parse_time("October 5, 2026"), "2026-10-05T00:00:00")
        self.assertEqual(amazon.parse_time("2026-10-05T22:00:00"), "2026-10-05T22:00:00")
        self.assertEqual(amazon.parse_time("Tomorrow by 10 PM"), "Tomorrow by 10 PM")

    def test_unparseable_estimate_becomes_a_note(self):
        body = fixture("amazon-in-transit.json")
        tracker = json.loads(body["progressTracker"])
        tracker["expectedDeliveryDate"] = "Tomorrow by 10 PM"
        tracker["summary"]["metadata"].pop("expectedDeliveryDate")
        body["progressTracker"] = json.dumps(tracker)
        r = amazon.parse("TBA000000000000", body)
        self.assertIsNone(r.eta)
        self.assertEqual(r.note, "Amazon expects delivery: Tomorrow by 10 PM")

    def test_status_rules(self):
        self.assertEqual(amazon.classify("OUT_FOR_DELIVERY"), "out_for_delivery")
        self.assertEqual(amazon.classify("RETURNED_TO_SENDER"), "returned")
        self.assertEqual(amazon.classify("DELIVERY_ATTEMPTED"), "exception")
        self.assertEqual(amazon.classify("Delivered"), "delivered")
        self.assertIsNone(amazon.classify("IN_TRANSIT"))


class Ship24Parse(unittest.TestCase):
    def test_usps_in_transit(self):
        r = ship24.parse("9400100000000000000000", fixture("ship24-usps.json"))
        self.assertEqual(r.carrier, "usps")
        self.assertEqual(r.status, "in_transit")
        self.assertEqual(r.eta.date, "2026-10-06")
        self.assertEqual(r.events[0].description, "Departed USPS Regional Facility")
        self.assertEqual(r.status_text, "Departed USPS Regional Facility")
        self.assertIn("usps.com", r.tracking_url)

    def test_pending_without_events_is_not_found(self):
        r = ship24.parse("9400100000000000000000", fixture("ship24-pending.json"), carrier_hint="usps")
        self.assertEqual(r.status, "not_found")
        self.assertEqual(r.carrier, "usps")

    def test_courier_codes(self):
        self.assertEqual(ship24.carrier_from_courier("us-post"), "usps")
        self.assertEqual(ship24.carrier_from_courier("dhl-express"), "dhl")
        self.assertEqual(ship24.carrier_from_courier("ontrac"), "ontrac")


if __name__ == "__main__":
    unittest.main()
