# parcel-watch

Package tracking with up-to-date delivery estimates, for people and agents. parcel-watch asks the carriers' own tracking APIs (FedEx, UPS), Amazon's public tracker, and Ship24 for everything else. It returns one normalized answer: status, expected delivery date or window, and recent scans. A watch list reports what changed since the last check, so a scheduled routine can tell you when a delivery estimate moves.

It's one Python package with no dependencies beyond the standard library (Python 3.9+). It works as a command-line tool and as an MCP server, and comes with a Grok plugin and skill.

**Why not just read the tracking page?** fedex.com loads tracking data from an API behind Akamai Bot Manager, which refuses scripts, headless browsers, and often real browsers on cloud hosts. FedEx's developer API isn't behind that wall, and it's free. See [docs/research.md](docs/research.md).

**Status:** the FedEx, UPS, and Ship24 code is tested against fixtures built from each API's published schema, but not yet against live credentials. The Amazon tracker has been checked live.

## Sources

| Source | Covers | Needs | Cost |
| --- | --- | --- | --- |
| `fedex-api` | FedEx Express, Ground, Ground Economy, Freight | FedEx developer keys | Free, 100,000 calls a day |
| `ups-api` | UPS | UPS developer keys | Free |
| `amazon-public` | Amazon Shipping (`TBA…`) | Nothing | Free, undocumented endpoint |
| `ship24` | USPS, DHL, OnTrac, and many other carriers; also the fallback for the sources above | Ship24 API key | Free plan: 10 new numbers a month |

parcel-watch guesses the carrier from the number's format and asks that carrier's source first. If the source isn't configured, has no record, or fails, it asks Ship24. Every result lists the sources it tried and what each one said.

## Setup

Put credentials in `credentials.env` in the state directory, one `KEY=VALUE` per line, and make the file private (`chmod 600`). The state directory is `$PARCEL_WATCH_HOME`, or `~/.config/parcel-watch` by default. Environment variables override the file. Configure only the sources you want:

```bash
mkdir -p -m 700 ~/.config/parcel-watch
install -m 600 /dev/null ~/.config/parcel-watch/credentials.env
$EDITOR ~/.config/parcel-watch/credentials.env
bin/parcel-watch doctor          # what's configured
bin/parcel-watch doctor --live   # also signs in to FedEx and UPS
```

```ini
FEDEX_CLIENT_ID=...
FEDEX_CLIENT_SECRET=...
UPS_CLIENT_ID=...
UPS_CLIENT_SECRET=...
SHIP24_API_KEY=...
```

**FedEx** ([developer.fedex.com](https://developer.fedex.com)):
1. Log in with a fedex.com user ID, or sign up for one.
2. Create an organization and an API project with **Basic Integrated Visibility** (the Track API).
3. The project starts with test keys. Set `FEDEX_SANDBOX=1` to use them; the sandbox returns canned data.
4. Production keys need a 9-digit FedEx account number linked to the organization.

**UPS** ([developer.ups.com](https://developer.ups.com)):
1. Log in with a ups.com ID.
2. Go to **Apps → Add Apps**, and link a UPS shipper account number (or create one).
3. Add the **Tracking** product.
4. Copy the client ID and secret. `UPS_SANDBOX=1` uses the test environment.

**Ship24** ([ship24.com](https://www.ship24.com)): sign up, then create an API key in the dashboard. Each new tracking number counts against the plan, so parcel-watch asks Ship24 last.

## Use

Run from a checkout with `bin/parcel-watch`, or install with `pip install .` to get `parcel-watch` on your `PATH`. `scripts/build-pyz.sh` builds `dist/parcel-watch.pyz`, a single file that runs with `python3`.

```console
$ parcel-watch track 1Z999AA10123456784
UPS 1Z999AA10123456784: In transit (On the Way)
  Expected: Tue Oct 6, between 10:30 AM and 2:30 PM (scheduled)
  Sat Oct 3, 8:12 AM: Departed from Facility - Louisville, KY, US
  Source: ups-api  https://www.ups.com/track?tracknum=1Z999AA10123456784
```

| Command | What it does |
| --- | --- |
| `track NUMBER... [--carrier C] [--json]` | Look up now |
| `add NUMBER [--label TEXT]` | Watch a package, and look it up now |
| `list` | Watched packages, as of their last check (no network calls) |
| `check [--quiet] [--force] [--min-age M]` | Refresh watched packages that aren't delivered, and print only what changed: status, delivery estimate, new scans. `--quiet` prints nothing when nothing changed. Packages checked in the last 15 minutes are skipped |
| `remove NUMBER` | Stop watching |
| `doctor [--live]` | Show which sources are set up |
| `mcp` | MCP server on stdin/stdout |

Delivered packages stop being checked, and drop off the list 7 days after delivery. `--json` gives the full result: `status` (one of `label_created`, `in_transit`, `out_for_delivery`, `available_for_pickup`, `delivered`, `exception`, `returned`, `unknown`, `not_found`, `error`), `eta` (`date`, `start`, `end`, `kind`), `delivered_at`, `delayed`, `events`, `note`, `error`, `tried`.

Delivery windows are in the delivery area's local time. FedEx usually includes the UTC offset; UPS and Amazon don't.

## Agents

- **MCP tools:** `track_package`, `watch_package`, `list_watched`, `unwatch_package`, `check_watched`. Start the server with `parcel-watch mcp`.
- **Skill:** [`plugin/skills/parcel-watch/SKILL.md`](plugin/skills/parcel-watch/SKILL.md) covers when to use the tools, how to report estimates, and how to set up a routine that announces changes.
- **Install:** [`plugin/README.md`](plugin/README.md) covers Grok Bot and the Grok CLI.

## Privacy

Tracking numbers go to the source being asked: FedEx, UPS, Amazon, or Ship24. Results keep only city, state, and country for scan locations. Recipient names, street addresses, postal codes, signatures, and photos are dropped. OAuth tokens are cached in `tokens.json` and the watch list is kept in `watchlist.json`, both mode 600 in the state directory. Nothing else is stored or sent.

## Development

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT
