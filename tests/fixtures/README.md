# Fixtures

Synthetic carrier replies. Every private field (names, streets, postal codes) is a `PRIVATE…`
placeholder, and the tests check that none of them reach a result.

| File | Built from |
| --- | --- |
| `fedex-*.json` | FedEx Track API v1 OpenAPI spec (Basic Integrated Visibility), `POST /track/v1/trackingnumbers` |
| `ups-*.json` | UPS `Tracking.yaml` in [UPS-API/api-documentation](https://github.com/UPS-API/api-documentation), `GET /api/track/v1/details/{inquiryNumber}` |
| `amazon-not-found.json` | A real `track.amazon.com/api/tracker/` reply for a made-up number |
| `amazon-in-transit.json`, `amazon-delivered.json` | The shape of real replies (string-encoded `progressTracker` and `eventHistory`), with invented values |
| `ship24-*.json` | Ship24's documented `POST /public/v1/trackers/track` fields, with invented values |

None of these came from a live carrier API with real credentials. Replace them with redacted
live replies once there are keys to record them with.
