# parcel-watch plugin

This folder gives an agent the parcel-watch MCP tools and a skill that explains how to use them (`skills/parcel-watch/SKILL.md`). The MCP server is `parcel-watch mcp`. It runs only while the agent uses it, and nothing needs to stay running between lookups. For "tell me when it changes," the skill has the agent create one routine that runs `check_watched`.

## What you need

- **Python 3.9 or newer** on the computer that runs the tools. parcel-watch uses only the standard library.
- **Keys for the sources you want** (see the main [README](../README.md#setup)):
  - FedEx and UPS developer keys. They're free; both need a carrier account number.
  - A Ship24 API key for USPS, DHL, and other carriers.
  - Amazon's public tracker needs no key.

## Network and credentials

- **Outbound HTTPS only,** to `apis.fedex.com`, `onlinetools.ups.com`, `track.amazon.com`, and `api.ship24.com` (or the FedEx and UPS sandboxes when `FEDEX_SANDBOX` or `UPS_SANDBOX` is set). Nothing listens on the network.
- **Credentials:** `FEDEX_CLIENT_ID`, `FEDEX_CLIENT_SECRET`, `UPS_CLIENT_ID`, `UPS_CLIENT_SECRET`, `SHIP24_API_KEY`. They go in `credentials.env` (mode 600) in the state directory, or in the environment. Tool arguments never carry them.
- **Keys only for parcel-watch:** use a FedEx project and a UPS app that have only the tracking product, so a leaked key can't ship or bill.

## Install on Grok Bot

Grok Bot doesn't install plugins from a git repository, so you give the bot the pieces. Grok Bot's official documentation doesn't cover custom MCP servers yet; step 3 follows a [third-party guide](https://composio.dev/content/how-to-add-mcp-servers-to-grok-bot), so check the result with step 5.

1. **Put parcel-watch under `/workspace`** on the bot's computer. Files there survive computer updates. In a chat with the bot:
   > Run `python3 --version`, then `git clone https://github.com/calebtt/parcel-watch /workspace/parcel-watch`, then `mkdir -p -m 700 /workspace/parcel-watch/state`.

   It needs Python 3.9 or newer.
2. **Add the keys.** Create `/workspace/parcel-watch/state/credentials.env` with mode 600 (`install -m 600 /dev/null …`), and put your keys in it, one `KEY=VALUE` per line. Keys you send in chat stay in the chat history, so use keys made only for parcel-watch. Then check:
   > Run `PARCEL_WATCH_HOME=/workspace/parcel-watch/state /workspace/parcel-watch/bin/parcel-watch doctor --live`
3. **Add the MCP server.** In a chat with the bot:
   > Add a custom MCP server called parcel-watch that runs: `env PARCEL_WATCH_HOME=/workspace/parcel-watch/state /workspace/parcel-watch/bin/parcel-watch mcp`

   It's listed under Settings → Plugins → Yours, and every bot on your account can use it.
4. **Add the skill:**
   > Create a skill called parcel-watch from the instructions in /workspace/parcel-watch/plugin/skills/parcel-watch/SKILL.md
5. **Check it:** give the bot a tracking number and ask when it will arrive. The answer should name its source, such as `fedex-api`.
6. **For change alerts, allow the parcel-watch tools without approval** (Auto-review: Always allow). A routine's approval request expires after about 10 minutes, so a routine waiting on one never reports anything.

To update, ask the bot to run `git -C /workspace/parcel-watch pull`. The state directory is ignored by git, so a pull keeps it.

## Install on the Grok CLI

1. Install the command so it's on your `PATH`: `pip install --user git+https://github.com/calebtt/parcel-watch` (or `pipx install …`). Then create `~/.config/parcel-watch/credentials.env` (main README, "Setup").
2. Add this repository as a marketplace and install the plugin:
   ```bash
   grok plugin marketplace add calebtt/parcel-watch
   grok plugin install parcel-watch --trust
   ```
   The Grok CLI installs no plugin without `--trust`.
