# MercadoLibre MCP Server

[🇺🇸 English](README.md) | [🇪🇸 Español](README.es.md)

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that wraps the [MercadoLibre REST API](https://developers.mercadolibre.com) — giving AI assistants the ability to search, create, update, delete, and manage product listings, orders, shipping, questions, advertising campaigns, and more across **18 countries** in Latin America.

## Why?

MercadoLibre already publishes an [official MCP server](https://developers.mercadolibre.com.uy/es_ar/mcp-server), but it only exposes **documentation search tools** — it cannot interact with the API on your behalf. This server fills that gap by wrapping **130+ REST API endpoints** as MCP tools that an AI assistant can call directly.

Credentials and tokens are loaded from environment variables **never** passed through the LLM prompt, so your API keys stay secure.

## Features

- **Listings (CRUD)** — search, get, create, update, close, relist items
- **Multi-country** — 18 sites: Argentina (MLA), Uruguay (MLU), Brazil (MLB), Mexico (MLM), Chile (MLC), and more
- **Orders** — search and view seller orders
- **Shipping** — get shipping methods, track shipments
- **Categories** — browse, get details, predict the best category for a product
- **Questions** — list and answer buyer questions
- **Advertising** — list Mercado Ads campaigns
- **Metrics** — get item visits and analytics
- **User profiles** — get seller/buyer reputation

## Sources & Documentation

This server is built against the official MercadoLibre API:

| Resource | URL |
|---|---|
| MercadoLibre Developers Portal | https://developers.mercadolibre.com |
| API Docs (ES) | https://developers.mercadolibre.com.uy/es_ar/api-docs-es |
| API Docs (EN) | https://developers.mercadolibre.com.uy/en_us/api-docs |
| Authentication & OAuth | https://developers.mercadolibre.com.uy/es_ar/autenticacion-y-autorizacion |
| Items & Search | https://developers.mercadolibre.com.uy/es_ar/items-y-busquedas |
| Orders | https://developers.mercadolibre.com.uy/es_ar/gestiona-ventas |
| Shipping | https://developers.mercadolibre.com.uy/es_ar/mercado-envios |
| Mercado Ads | https://developers.mercadolibre.com.uy/es_ar/introduccion-a-mercado-ads |
| Rate Limits | https://developers.mercadolibre.com.uy/es_ar/rate-limit-error-429 |

## Prerequisites

- **Python 3.11+** with [uv](https://docs.astral.sh/uv/) installed
- A **MercadoLible seller account** (to use authenticated operations)
- A **MercadoLibre Application** (free — created in the developer portal)

## Installation

```bash
# Clone
git clone https://github.com/dedero1985/mercadolibre-mcp.git
cd mercadolibre-mcp

# Install dependencies
uv sync

# Verify everything works
uv run python -c "from mercadolibre_mcp.main import mcp; print('MCP server ready')"
```

## Credential Setup

### 1. Create a MercadoLibre Application

1. Go to [https://developers.mercadolibre.com/apps](https://developers.mercadolibre.com/apps)
2. Click **"Crear aplicación"** (Create Application)
3. Fill in:
   - **Application Name**: e.g., `mercadolibre-mcp`
   - **Description**: Short description of your use
   - **Redirect URI**: Use the exact, static URL registered for your application. Do not assume the CLI's `http://localhost:8080/callback` default is registered or accepted for your app.
4. After creation, you'll get:
   - **`App ID`** (client_id) — a numeric ID
   - **`Secret Key`** (client_secret) — a long alphanumeric string
5. Under **"Permisos funcionales"** (Functional Permissions), check at minimum:
   - `read` — for reading items, orders, etc.
   - `write` — for creating/updating listings
   - `offline_access` — so the token keeps working after you leave
6. Save the changes.

### 2. Configure Environment Variables

Copy the example file:

```bash
umask 077
cp .env.example .env
chmod 600 .env
```

Edit `.env` with your credentials:

```env
MERCADOLIBRE_CLIENT_ID=1234567890
MERCADOLIBRE_CLIENT_SECRET=your_secret_key_here
MERCADOLIBRE_REDIRECT_URI=https://your-registered-callback.example/callback
MERCADOLIBRE_SITE_ID=MLA
```

| Variable | Required | Description |
|---|---|---|
| `MERCADOLIBRE_CLIENT_ID` | ✅ Yes | Your App ID from the developer portal |
| `MERCADOLIBRE_CLIENT_SECRET` | ✅ Yes | Your Secret Key |
| `MERCADOLIBRE_REDIRECT_URI` | ✅ Yes | Must match what you registered in the app |
| `MERCADOLIBRE_SITE_ID` | ❌ No | **Default/fallback** site used when a tool call doesn't specify one (e.g. MLA, MLU). Not a restriction — see multi-country setup below. |
| `LOG_LEVEL` | ❌ No | `INFO`, `DEBUG`, `WARNING`, `ERROR` |

The Python modules do **not** automatically load `.env`. From the repository directory, use `uv run --env-file .env ...` as shown below, or supply the variables through a secure process environment. For client launch commands, add `--env-file` and the absolute path to `.env` after `run` unless the client already inherits the variables. Never commit `.env`, paste credentials into chat, or put secrets in command-line arguments.

See the [official OAuth documentation](https://developers.mercadolibre.com.uy/es_ar/autenticacion-y-autorizacion): authorize with the account owner/administrator, not a collaborator; the redirect URI must match exactly. **Keep PKCE required in your MercadoLibre app.** This CLI always uses PKCE with `S256` and validates OAuth `state`; no additional flag or dependency is needed. Refresh tokens are single-use and tied to the issuing App ID, so do not share them with an old integration or reuse tokens from a different app.

### 3. Run OAuth Setup — once per country

**One app, multiple tokens.** The `MERCADOLIBRE_CLIENT_ID` / `MERCADOLIBRE_CLIENT_SECRET` above are shared across **all 18 countries** — you register the application only once, and the same `.env` values work everywhere. However, the **access token** produced by the OAuth flow belongs to one specific MercadoLibre **seller account**, and seller accounts are normally registered under a single home country. If you sell in both Argentina and Uruguay with two separate accounts, you must authorize **each one separately** — same app credentials, two different tokens.

Run the setup once per country you operate in:

```bash
# Authorize your Argentina account
uv run --env-file .env python -m mercadolibre_mcp.auth --site-id MLA

# Authorize your Uruguay account
uv run --env-file .env python -m mercadolibre_mcp.auth --site-id MLU

# ...repeat for any other country/account you have
```

When a new authorization is needed, setup will:
1. Generate a fresh random PKCE verifier and `state`, then open your browser with the `S256` challenge to authorize that country's account
2. Ask you to paste the redirected URL into a **hidden-input** terminal prompt; validate its target, `state`, and authorization code before exchanging the code with the verifier
3. Save an access token to `~/.mercadolibre_mcp/profiles/<SITE_ID>.json` (e.g. `MLA.json`, `MLU.json`)

Run OAuth in your own interactive terminal. Paste the redirected URL only into that terminal, never into an AI conversation. The CLI opens a browser; it does not start a callback HTTP server.

The verifier and state exist only for that setup attempt: do not close the CLI before pasting the callback. Missing, duplicate, or blank `code`/`state`, mismatched state, OAuth error replies, fragments, and unexpected redirect targets are rejected without exchanging the code. The callback must preserve any registered static query parameters; use an ASCII HTTP(S) redirect URI without fragments or reserved OAuth response parameters (`code`, `state`, `error`, `error_description`, `error_uri`). A registered URI with an empty path and its browser-normalized `/` form are treated as the same target; the exact registered string is still sent to the token endpoint.

If the browser cannot launch or hidden input is unavailable, setup stops rather than printing the authorization URL or echoing the callback. Configure a working browser in your local desktop session and rerun from a private terminal. After any rejected callback, rerun setup and use the new callback, not one from an earlier attempt. Existing token refresh and noninteractive MCP calls do not open a browser.

Tokens are saved **locally on your machine**, one file per country, and are **never sent to the LLM**. The MCP server uses them server-side to authenticate API calls, refreshing each one automatically as it expires.

Check which countries are already authorized at any time:

```bash
uv run python -m mercadolibre_mcp.auth --list
```

Or just ask your AI assistant — *"Which MercadoLibre countries am I authenticated in?"* — which uses the built-in `list_authenticated_sites` tool.

Every tool accepts an optional `site_id` argument that picks which cached profile executes the call (e.g. "list my listings in Uruguay" → `site_id="MLU"`); if omitted, it falls back to `MERCADOLIBRE_SITE_ID` (or `MLA`).

### Multiple seller accounts in the same country

A site has one **default** account plus any number of **aliased** accounts. Profiles are stored as:

```
~/.mercadolibre_mcp/profiles/MLA.json            # default account for MLA
~/.mercadolibre_mcp/profiles/MLA__business.json  # aliased account for MLA
```

Authorize an additional account for a country you already use by adding `--account <alias>`:

```bash
uv run --env-file .env python -m mercadolibre_mcp.auth --site-id MLA --account business
```

Then pass the same alias on tool calls (`site_id="MLA"`, `account="business"`). Omit `account` to use the site's default account; `MERCADOLIBRE_ACCOUNT` sets a fallback alias for the server. Aliases are limited to 1-32 characters from `A-Z`, `a-z`, `0-9`, `_`, `-` and are validated so they can never escape the profiles directory.

Writes (`create_item`, `update_item`, `delete_item`, `relist_item`) must use the alias that owns the listing. After authorizing a new alias, restart OpenCode so the in-memory client cache is rebuilt.

List what is authorized, including aliases:

```bash
uv run --env-file .env python -m mercadolibre_mcp.auth --list
```

> **Advanced / not used here:** MercadoLibre also offers an official ["Global Selling" cross-border program](https://global-selling.mercadolibre.com) where a single approved merchant account can operate across Mexico, Brazil, Chile, Colombia, and Argentina with **one** token. It requires special onboarding with MercadoLibre and does **not** officially cover Uruguay, so it isn't used by this server — the standard per-country flow above works for any seller without special enrollment.

---

## Client Configuration

### Claude Desktop / Claude Code

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `~/.config/Claude/claude_desktop_config.json` (Linux):

```json
{
  "mcpServers": {
    "mercadolibre": {
      "command": "uv",
      "args": [
        "--directory",
        "/ABSOLUTE/PATH/TO/mercadolibre-mcp",
        "run",
        "python",
        "-m",
        "mercadolibre_mcp.main"
      ]
    }
  }
}
```

**Important**: Do **not** put your credentials in the JSON config. They go in the `.env` file or in your shell profile:

```bash
# Add to ~/.zshrc or ~/.bashrc
export MERCADOLIBRE_CLIENT_ID="your_app_id"
export MERCADOLIBRE_CLIENT_SECRET="your_secret"
export MERCADOLIBRE_SITE_ID="MLA"
```

Then in your MCP config, reference the env:

```json
{
  "mcpServers": {
    "mercadolibre": {
      "command": "uv",
      "args": ["--directory", "/path/to/mercadolibre-mcp", "run", "python", "-m", "mercadolibre_mcp.main"],
      "env": {
        "MERCADOLIBRE_CLIENT_ID": "${MERCADOLIBRE_CLIENT_ID}",
        "MERCADOLIBRE_CLIENT_SECRET": "${MERCADOLIBRE_CLIENT_SECRET}",
        "MERCADOLIBRE_SITE_ID": "${MERCADOLIBRE_SITE_ID:-MLA}"
      }
    }
  }
}
```

### Cursor

Add to `~/.cursor/mcp_config.json`:

```json
{
  "mcpServers": {
    "mercadolibre": {
      "command": "uv",
      "args": ["--directory", "/ABSOLUTE/PATH/TO/mercadolibre-mcp", "run", "python", "-m", "mercadolibre_mcp.main"]
    }
  }
}
```

### Windsurf

Add to `~/.windsurf/mcp_config.json` (same format as Cursor).

### OpenCode

Merge the following into `~/.config/opencode/opencode.json` (or `opencode.jsonc`) for global use, or a project-root `opencode.json` / `opencode.jsonc` for that project. Preserve unrelated settings. OpenCode uses the top-level `mcp` object, **not** `.opencode/mcp_servers.json` or `mcpServers`.

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "mercadolibre": {
      "type": "local",
      "command": [
        "/ABSOLUTE/PATH/TO/uv",
        "--directory", "/ABSOLUTE/PATH/TO/mercadolibre-mcp",
        "run", "--env-file", "/ABSOLUTE/PATH/TO/mercadolibre-mcp/.env",
        "python", "-m", "mercadolibre_mcp.main"
      ],
      "enabled": true,
      "timeout": 30000
    }
  }
}
```

Replace all paths with real absolute paths; `command -v uv` shows the executable location. `command` is one array containing the executable and all arguments; there is no separate `args` field. The local process communicates over stdio even though OpenCode's configuration type is `local`. Keep credentials in the protected `.env` file, not this JSON. Set `MERCADOLIBRE_SITE_ID=MLU` there for a Uruguay default; use `site_id="MLA"` for Argentina. One server supports both profiles.

Quit and restart OpenCode after saving, then run `opencode mcp list` to verify the connection. A connected MCP server does **not** mean either seller account is authorized: complete the per-country OAuth steps above and check `list_authenticated_sites`. `opencode mcp auth` handles remote MCP OAuth and is not the seller authorization flow for this local server.

Reference: [OpenCode MCP configuration](https://opencode.ai/docs/mcp-servers/) and [configuration schema](https://opencode.ai/config.json).

### Muster (if you use the Muster aggregator)

Create `/Users/external-bruno.ponce/.config/muster/mcpservers/mercadolibre.yaml`:

```yaml
apiVersion: muster.giantswarm.io/v1alpha1
kind: MCPServer
metadata:
  name: mercadolibre
  namespace: default
spec:
  autoStart: true
  command: /Users/external-bruno.ponce/.local/bin/uv
  args:
    - --directory
    - /ABSOLUTE/PATH/TO/mercadolibre-mcp
    - run
    - python
    - -m
    - mercadolibre_mcp.main
  env:
    PATH: /opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin
    MERCADOLIBRE_CLIENT_ID: your_app_id
    MERCADOLIBRE_CLIENT_SECRET: your_secret
    MERCADOLIBRE_SITE_ID: MLA
  timeout: 120
  type: stdio
```

---

## Usage Examples

Once the server is connected, you can ask your AI assistant to do things like:

### Product Search

> "Find iPhone 15 Pro Max in Argentina, under 2000 USD"
> "Search for zapatillas running in Uruguay, priced between 1000 and 5000 UYU"
> "Show me laptops available in Brazil"

### Listings Management

> "Create a new listing: iPhone 15, 128GB, new, 1500 ARS, category MLA1051, quantity 5"
> "Update the price of item MLA1234567890 to 2000"
> "Close listing MLB987654321"
> "Show all my active listings"
> "Relist my finished ad for item MLA1234567890"

### Categories

> "What categories are available in Uruguay?"
> "Tell me about category MLU1000"
> "What category should I use for 'Zapatillas Nike Running Hombre'?"

### Orders & Shipping

> "Show me my recent orders"
> "What's the status of order 1234567890?"
> "Track shipment 987654321"
> "What shipping options are available for item MLA123 to zip code 11000?"

### Questions

> "Show me unanswered questions for my items"
> "Answer question 98765 with 'Yes, we have stock'"

### Advertising & Analytics

> "Show my active Mercado Ads campaigns"
> "How many visits does item MLA1234567890 have?"
> "Show me last week's visits for my top item"

### Multi-Country Status

> "Which MercadoLibre countries am I authenticated in?"
> "Am I set up for Uruguay yet?"
> "Show me my connected MercadoLibre accounts"

---

## Available Tools

| Tool | Description |
|---|---|
| `search_items` | Search products by keyword, category, price, condition |
| `get_item` | Full listing details (pictures, shipping, seller) |
| `create_item` | Create a new listing |
| `update_item` | Update price, stock, title, description |
| `delete_item` | Close/finish a listing |
| `list_my_items` | All your listings, filterable by status |
| `relist_item` | Relist a closed item |
| `list_categories` | Top-level categories for a site |
| `get_category` | Category details and attributes |
| `predict_category` | Best category match for a product title |
| `search_orders` | Seller orders, filterable by status |
| `get_order` | Full order details |
| `get_shipping_methods` | Available shipping options for an item+zip |
| `get_shipment` | Track a shipment |
| `list_questions` | Questions on your items |
| `answer_question` | Answer a buyer question |
| `get_user` | User profile and seller reputation |
| `list_ads_campaigns` | Mercado Ads campaigns |
| `get_item_visits` | Visit statistics for a listing |
| `list_authenticated_sites` | List which countries have a cached, ready-to-use token — and which don't |

All tools accept an optional `site_id` parameter selecting which authenticated country profile executes the call (e.g. `MLA`, `MLU`). Public read operations (search, categories) can be served by any authenticated profile; writes (`update_item`, `delete_item`, `create_item`, etc.) must use the profile that actually owns the account/listing.

---

## Security

- **PKCE S256 and OAuth state**: Every interactive authorization uses a fresh 256-bit verifier and state. The callback's target and state are checked before the verifier is sent to MercadoLibre's token endpoint. Keep PKCE enabled in the app settings.
- **Private authorization input**: Callback paste is hidden; the CLI does not print the authorization URL, callback, verifier, or state. OAuth rejection messages do not echo provider-controlled error text.
- **Credentials never reach the LLM**: API keys and secrets are loaded from environment variables or `.env` files and used only in the MCP server process
- **OAuth tokens cached locally, one file per country**: Each site's access/refresh token lives in its own file under `~/.mercadolibre_mcp/profiles/<SITE_ID>.json` with `chmod 600` permissions and atomic writes (a crash mid-write never corrupts a profile)
- **No interactive hang**: If a tool is called for a country that hasn't been authorized yet, the server returns a clear error telling you which `auth --site-id` command to run — it never blocks waiting for browser input during a live tool call
- **Auto-refresh**: Expired tokens are refreshed automatically server-side, per profile
- **No credential logging**: Client credentials are never written to logs
- **Rate limited**: The server respects MercadoLibre's API rate limits (1500 req/min general, 100 req/min for orders) with automatic retry on 429 responses

## Project Structure

```
mercadolibre-mcp/
├── README.md                    # This file
├── README.es.md                 # Spanish version
├── pyproject.toml               # Python project config
├── .env.example                 # Environment template
├── .gitignore
├── src/
│   └── mercadolibre_mcp/
│       ├── __init__.py
│       ├── main.py              # FastMCP server + tool definitions
│       ├── client.py            # HTTP client (auth injection, rate limiting)
│       └── auth.py              # OAuth2 token management
└── .venv/                       # Virtual environment (uv)
```

Runtime data (not part of the repo, created on first use):

```
~/.mercadolibre_mcp/
└── profiles/
    ├── MLA.json                 # Argentina token (chmod 600)
    ├── MLU.json                 # Uruguay token (chmod 600)
    └── ...                      # one file per authorized country
```

## Testing

Run the offline OAuth security and regression tests from the repository directory:

```bash
uv run python -m unittest discover -s tests -v
```

Expected result: the command exits successfully and the unittest summary ends with `OK`. Any failure must be investigated before using or publishing the change.

Tests cover the RFC 7636 S256 vector, fresh randomness, both Argentina and Uruguay authorization domains, a mocked code exchange, callback/state rejection (including trailing-slash normalization and value-free mismatch diagnostics), hidden input, redacted errors, browser-launcher output suppression, account-alias validation and per-alias profile isolation, and cached/refresh/noninteractive behavior. They do not access real credentials, profiles, browsers, or the MercadoLibre API; the launcher regression uses a fake browser subprocess.

To check the installed MCP separately:

1. Quit and restart OpenCode after configuring the server.
2. Run `opencode mcp list`; expect `mercadolibre` to be connected. This checks startup, not seller authorization.
3. Ask OpenCode to call `list_authenticated_sites` through MercadoLibre MCP. If using a raw MCP client, its arguments are `{"input": {}}`. An empty profile list is normal before OAuth.
4. Complete the local OAuth setup above for `MLU` and `MLA`, keeping PKCE required, then repeat the status check. Browser consent and the exact registered redirect URI are required; never paste callbacks into chat.
5. Optionally request a read-only account operation for each country, such as listing your items. This verifies API access; do not create, edit, or close listings just to test installation.

Passing offline tests or seeing a connected server does not prove that live account authorization is complete.

## License

MIT

## Disclaimer

This project is **not affiliated with, endorsed by, or sponsored by MercadoLibre S.R.L.** It is an independent integration built on top of MercadoLibre's public REST API. Use at your own risk and in compliance with MercadoLibre's [Terms & Conditions](https://developers.mercadolibre.com.uy/es-uy-terminos-y-condiciones).
