# Why scraping carrier tracking pages fails, and what works

Checked 2026-10-03 from a residential connection, with `curl` and the published API documentation.

## FedEx: the tracking page's data API sits behind Akamai Bot Manager

`fedex.com/fedextrack/?trknbr=…` redirects to `/wtrk/track/`, an Angular single-page app. The HTML has no tracking data. After the page loads, its JavaScript asks for an OAuth token (`POST /auth/oauth/v2/token`, client credentials from the app's config) and then calls `POST https://api.fedex.com/track/v2/shipments` for the data.

Akamai Bot Manager guards both the page and that API:

- The first response sets Akamai's `_abck` and `bm_sz` cookies. A fresh `_abck` ends in `~-1~…`, which means the client hasn't been validated.
- The page loads an obfuscated sensor script from a random-looking path (`/assets/<hash>`). In a real browser, it collects device and behavior telemetry (browser APIs, canvas, timing, mouse and keyboard events) and posts it back to Akamai. Only then does `_abck` become valid.
- Without a valid `_abck`, `api.fedex.com/track/v2/shipments` returns `403 Access Denied` from the Akamai edge (`server: AkamaiGHost`). The request never reaches FedEx's API gateway. A browser User-Agent and FedEx `Origin`/`Referer` headers don't change that:

  ```
  $ curl -X POST https://api.fedex.com/track/v2/shipments -H 'Content-Type: application/json' -d '{}'
  HTTP/2 403
  server: AkamaiGHost
  <TITLE>Access Denied</TITLE> … Reference #18.7931ab8…
  ```

- Akamai also scores the TLS and HTTP/2 fingerprint and the IP's reputation. That's why headless browsers and cloud hosts, such as an agent's cloud computer, fail more often. Pages still load, but the data call is refused, so the page shows "unable to find tracking number" whether the number exists or not.

An independent open-source tracker, [plhery/universal-parcel-scraper](https://github.com/plhery/universal-parcel-scraper) (`carriers/fedex/README.md`), reports the same thing. Plain HTTP gets 403 even with the page's cookies, headers, and token. Akamai "often refuses even real browser sessions," from datacenter and residential IPs alike. Copying cookies into a new browser context doesn't carry the acceptance over. It documents the same pattern for UPS (`webapis.ups.com/track/api/Track/GetStatus`): Akamai holds the call open until it times out.

Getting past this would mean imitating Akamai's sensor. That breaks whenever Akamai updates the script, and it's unreliable even for real browsers. It also works against the site's own controls. parcel-watch doesn't do this.

## FedEx and UPS publish free tracking APIs

`apis.fedex.com` is FedEx's developer API. It's served by a Layer7 API gateway, not Akamai, and answers a request without a token with an ordinary JSON `401`.

| Source | Cost and limits | What you need | Delivery estimate fields |
| --- | --- | --- | --- |
| **FedEx Basic Integrated Visibility** (Track API), `POST apis.fedex.com/track/v1/trackingnumbers` | Free. 100,000 calls per day, 1,400 per 10 seconds, up to 30 numbers per request | A developer.fedex.com project; production keys require linking a 9-digit FedEx account number | `estimatedDeliveryTimeWindow.window.{begins,ends}`, `dateAndTimes[type=ESTIMATED_DELIVERY]`, `COMMITMENT`, `standardTransitTimeWindow`, delay flags in `delayDetail` |
| **UPS Tracking API**, `GET onlinetools.ups.com/api/track/v1/details/{n}` | Free to license; no published volume cap | A developer.ups.com app (client ID and secret) linked to a UPS shipper account | `deliveryDate[type=SDD/RDD/DEL]`, `deliveryTime[type=EDW/CDW/IDW/CMT/EOD]` with start and end times |
| **USPS APIs** v3 Tracking, `apis.usps.com` | Free | A USPS business account. Tracking isn't in the default "Public Access" product: you request it through USPS's Email Us form and fill in a usage questionnaire (see [USPS/api-examples#28](https://github.com/USPS/api-examples/issues/28)) | Not implemented here yet |
| **Amazon public tracker**, `GET track.amazon.com/api/tracker/{n}` | No key; undocumented | Nothing | `progressTracker.expectedDeliveryDate`, `hasDeliveryDelayed` |
| **Ship24**, `POST api.ship24.com/public/v1/trackers/track` | Free plan: 10 shipments a month (100 in the first month). Pro: $39 a month for 1,000 | An API key | `shipment.delivery.estimatedDeliveryDate`, `statusMilestone` |

Other aggregators checked: 17TRACK (accounts created after 2026-01-07 get 200 free numbers once; a real-time refresh costs 10 quota), Shippo ($0.02 per track), EasyPost ($0.02–0.03 per tracker not bought through EasyPost). AfterShip and TrackingMore offer API access only on paid plans.

## Amazon

`track.amazon.com/api/tracker/{TBA…}` answers plain HTTP with JSON. There's no bot wall, but it's undocumented. It covers **Amazon Shipping** parcels, where a third party ships with Amazon. Many **retail-order** TBA numbers exist only inside the buyer's account: the public tracker returns `TRACKING_ID_NOT_FOUND` for them, and the delivery estimate is in Your Orders and the shipping email. Orders that Amazon hands to UPS or USPS show that carrier's number on the order page, and that number works with the carrier's API.

## Decision

- FedEx and UPS go through their official APIs. They are free and give delivery windows, not just dates.
- Amazon uses the public tracker. When the tracker doesn't know the number, the result says so, and the skill falls back to the user's order email when the agent can read it.
- USPS, DHL, and everything else go through Ship24 for now. A direct USPS provider can come once a USPS tracking scope is approved.
- No page scraping, browser automation, or bot-protection workarounds.
