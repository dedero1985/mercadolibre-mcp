# MercadoLibre MCP Server - auth.py
"""OAuth 2.0 token management for MercadoLibre API — multi-profile (per-country).

MercadoLibre accounts are typically per-country: a seller with a local account
in Argentina and another in Uruguay needs to authorize EACH one separately,
even though both use the SAME registered application (client_id/client_secret).
This module caches one OAuth profile per site_id, so you can run the setup
flow once per country and the server will pick the right cached token based
on the site_id requested by a tool call.

Flow:
  1. Run `python -m mercadolibre_mcp.auth --site-id MLA` (once per country)
  2. User authorizes in the browser → redirect with code
  3. Exchange code for access + refresh tokens
  4. Store tokens in ~/.mercadolibre_mcp/profiles/{site_id}.json
  5. On each server run, load the cached profile for the requested site,
     auto-refresh if expired. NEVER blocks on interactive input while
     serving a live tool call (see `interactive` flag on ensure_token).

Credentials are loaded from environment variables (never from tool args),
so the LLM never has access to them.
"""

from __future__ import annotations

import base64
import getpass
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit

import httpx

logger = logging.getLogger(__name__)

TOKEN_DIR = Path.home() / ".mercadolibre_mcp"
PROFILES_DIR = TOKEN_DIR / "profiles"
LEGACY_TOKEN_FILE = TOKEN_DIR / "tokens.json"  # pre-multi-profile format (auto-migrated)

# MercadoLibre site → auth domain mapping
AUTH_DOMAINS: dict[str, str] = {
    "MLA": "auth.mercadolibre.com.ar",
    "MLB": "auth.mercadolibre.com.br",
    "MLM": "auth.mercadolibre.com.mx",
    "MLC": "auth.mercadolibre.cl",
    "MCO": "auth.mercadolibre.com.co",
    "MLU": "auth.mercadolibre.com.uy",
    "MPE": "auth.mercadolibre.com.pe",
    "MEC": "auth.mercadolibre.com.ec",
    "MLV": "auth.mercadolibre.com.ve",
    "MCR": "auth.mercadolibre.com.cr",
    "MPA": "auth.mercadolibre.com.pa",
    "MRD": "auth.mercadolibre.com.do",
    "MHN": "auth.mercadolibre.com.hn",
    "MBO": "auth.mercadolibre.com.bo",
    "MNI": "auth.mercadolibre.com.ni",
    "MPY": "auth.mercadolibre.com.py",
    "MSV": "auth.mercadolibre.com.sv",
    "MGT": "auth.mercadolibre.com.gt",
}

SITE_NAMES: dict[str, str] = {
    "MLA": "Argentina",
    "MLB": "Brasil",
    "MLM": "México",
    "MLC": "Chile",
    "MCO": "Colombia",
    "MLU": "Uruguay",
    "MPE": "Perú",
    "MEC": "Ecuador",
    "MLV": "Venezuela",
    "MCR": "Costa Rica",
    "MPA": "Panamá",
    "MRD": "República Dominicana",
    "MHN": "Honduras",
    "MBO": "Bolivia",
    "MNI": "Nicaragua",
    "MPY": "Paraguay",
    "MSV": "El Salvador",
    "MGT": "Guatemala",
}

ALL_SITE_IDS = list(AUTH_DOMAINS.keys())


def validate_site_id(site_id: str) -> None:
    """Raise ValueError if site_id is unknown."""
    if site_id not in AUTH_DOMAINS:
        raise ValueError(
            f"Unknown site_id '{site_id}'. "
            f"Supported sites: {', '.join(sorted(ALL_SITE_IDS))} "
            f"(e.g., MLA=Argentina, MLU=Uruguay, MLB=Brasil)"
        )


def _migrate_legacy_token_file() -> None:
    """One-time migration from the old single-profile tokens.json to per-site profile files.

    Safe to call repeatedly (no-op once migrated).
    """
    if not LEGACY_TOKEN_FILE.exists():
        return
    try:
        raw = LEGACY_TOKEN_FILE.read_text().strip()
        if not raw:
            LEGACY_TOKEN_FILE.unlink()
            return
        data = json.loads(raw)
        site_id = data.get("site_id") or os.environ.get("MERCADOLIBRE_SITE_ID", "MLA")
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            PROFILES_DIR.chmod(0o700)
        except OSError:
            pass
        target = PROFILES_DIR / f"{site_id}.json"
        if not target.exists():
            target.write_text(json.dumps(data, indent=2))
            target.chmod(0o600)
            logger.info("Migrated legacy token to profile '%s'", site_id)
        LEGACY_TOKEN_FILE.unlink()
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to migrate legacy token file: %s", exc)


class TokenStore:
    """Per-site OAuth token storage backed by ~/.mercadolibre_mcp/profiles/{site_id}.json.

    Each MercadoLibre country/site gets its own cached profile, since seller
    accounts (and therefore tokens) are typically per-country.
    """

    def __init__(self, site_id: str) -> None:
        validate_site_id(site_id)
        self.site_id = site_id
        self._path = PROFILES_DIR / f"{site_id}.json"
        self._data: dict[str, Any] = {}
        _migrate_legacy_token_file()
        self._load()

    # ── file I/O ──────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                raw = self._path.read_text().strip()
                if raw:
                    self._data = json.loads(raw)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load profile '%s': %s", self.site_id, exc)
                self._data = {}

    def _save(self) -> None:
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            PROFILES_DIR.chmod(0o700)
        except OSError:
            pass
        # Atomic write: temp file + rename, so a crash mid-write never corrupts the profile.
        tmp_path = self._path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(self._data, indent=2))
        tmp_path.chmod(0o600)
        tmp_path.replace(self._path)

    # ── public helpers ────────────────────────────────────────

    def has_token(self) -> bool:
        return bool(self._data.get("access_token"))

    def is_expired(self) -> bool:
        expires_at = self._data.get("expires_at")
        if not expires_at:
            return True
        try:
            return datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
        except (TypeError, ValueError):
            return True

    def get_access_token(self) -> str | None:
        return self._data.get("access_token")

    def get_refresh_token(self) -> str | None:
        return self._data.get("refresh_token")

    def get_user_id(self) -> int | None:
        return self._data.get("user_id")

    def update(self, token_data: dict[str, Any]) -> None:
        """Store a fresh token response and compute the expiration timestamp."""
        expires_in = token_data.get("expires_in", 21_600)  # default 6 h
        self._data = {
            "site_id": self.site_id,
            "access_token": token_data["access_token"],
            "token_type": token_data.get("token_type", "bearer"),
            "expires_in": expires_in,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=expires_in - 300)
            ).isoformat(),  # refresh 5 min early
            "scope": token_data.get("scope", ""),
            "user_id": token_data.get("user_id"),
            "refresh_token": token_data.get("refresh_token"),
        }
        self._save()

    def clear(self) -> None:
        self._data = {}
        if self._path.exists():
            self._path.unlink()

    @property
    def raw(self) -> dict[str, Any]:
        return dict(self._data)


def list_cached_sites() -> list[dict[str, Any]]:
    """Introspect all cached profiles without instantiating live clients or refreshing tokens."""
    _migrate_legacy_token_file()
    results: list[dict[str, Any]] = []
    if not PROFILES_DIR.exists():
        return results
    for f in sorted(PROFILES_DIR.glob("*.json")):
        site_id = f.stem
        try:
            data = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        if not data.get("access_token"):
            continue
        expires_at = data.get("expires_at")
        is_expired = True
        if expires_at:
            try:
                is_expired = datetime.fromisoformat(expires_at) <= datetime.now(timezone.utc)
            except (TypeError, ValueError):
                pass
        results.append(
            {
                "site_id": site_id,
                "site_name": SITE_NAMES.get(site_id, site_id),
                "user_id": data.get("user_id"),
                "has_refresh_token": bool(data.get("refresh_token")),
                "expires_at": expires_at,
                "is_expired": is_expired,
            }
        )
    return results


# ── OAuth helpers ─────────────────────────────────────────────


def _generate_pkce_verifier() -> str:
    """Return an RFC 7636 unreserved verifier with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def _pkce_challenge(code_verifier: str) -> str:
    """Derive the unpadded base64url S256 challenge from an RFC 7636 verifier."""
    if re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", code_verifier) is None:
        raise ValueError("Invalid PKCE verifier.")
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _parse_oauth_url(url: str) -> tuple[SplitResult, dict[str, list[str]]]:
    """Parse strictly, without urllib's silent whitespace/control normalization."""
    try:
        if (
            not url
            or any(char.isspace() or ord(char) < 32 or ord(char) >= 127 for char in url)
            or "\\" in url
            or "#" in url
            or re.search(r"%(?![0-9A-Fa-f]{2})", url)
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or "%" in parsed.netloc
            or parsed.netloc.endswith(":")
        ):
            raise ValueError
        # Accessing port also validates malformed or out-of-range port numbers.
        _ = parsed.port
        query = parse_qs(
            parsed.query, keep_blank_values=True, strict_parsing=True,
            errors="strict", max_num_fields=100,
        )
        return parsed, query
    except (ValueError, UnicodeError):
        raise RuntimeError("Invalid OAuth URL. Use the complete registered callback URL.") from None


def _validate_redirect_uri(redirect_uri: str) -> tuple[SplitResult, dict[str, list[str]]]:
    """Require an HTTP(S) URI without fragments or reserved OAuth query keys."""
    parsed, query = _parse_oauth_url(redirect_uri)
    if {"code", "state", "error", "error_description", "error_uri"}.intersection(query):
        raise RuntimeError("Registered redirect URI must not contain OAuth response parameters.")
    return parsed, query


def _validate_callback(redirected_url: str, redirect_uri: str, expected_state: str) -> str:
    """Return one code only after target, static query, OAuth error and state checks."""
    registered, static_query = _validate_redirect_uri(redirect_uri)
    callback, query = _parse_oauth_url(redirected_url)
    if (callback.scheme, callback.netloc, callback.path) != (
        registered.scheme, registered.netloc, registered.path
    ):
        raise RuntimeError("OAuth callback target does not match the registered redirect URI.")
    if any(sorted(query.get(key, [])) != sorted(values) for key, values in static_query.items()):
        raise RuntimeError("OAuth callback does not preserve the registered query parameters.")
    if {"error", "error_description", "error_uri"}.intersection(query):
        raise RuntimeError("OAuth authorization was not successful. Restart setup and authorize again.")
    for key in ("code", "state"):
        values = query.get(key, [])
        if len(values) != 1 or not values[0].strip():
            raise RuntimeError("OAuth callback requires exactly one nonblank code and state.")
    if not secrets.compare_digest(query["state"][0].encode("utf-8"), expected_state.encode("utf-8")):
        raise RuntimeError("OAuth state validation failed. Restart setup and use the new callback.")
    return query["code"][0]


def _open_authorization_browser(auth_url: str) -> bool:
    """Isolate browser launchers so subprocess diagnostics cannot expose the URL."""
    script = (
        "import sys, webbrowser; "
        "sys.exit(0 if webbrowser.open(sys.stdin.read()) else 1)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=auth_url,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _read_callback_url() -> str:
    """Fail closed if hidden terminal input is unavailable; never fall back to echo."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass("  Pasted URL (hidden) > ")
    except (getpass.GetPassWarning, EOFError, OSError):
        raise RuntimeError(
            "Hidden callback input unavailable. Retry setup in a private terminal."
        ) from None


def _build_authorization_url(
    client_id: str, redirect_uri: str, site_id: str, code_challenge: str, state: str,
) -> str:
    """Build the site's authorization URL with mandatory PKCE S256 and OAuth state."""
    auth_domain = AUTH_DOMAINS[site_id]
    params = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"https://{auth_domain}/authorization?{params}"


def _exchange_code_for_token(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    """Exchange a validated code using its verifier and the exact registered URI."""
    resp = httpx.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": code_verifier,
        },
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_access_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Refresh an expired access token using the refresh token."""
    resp = httpx.post(
        "https://api.mercadolibre.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        },
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()


# ── Setup (interactive) / server-side (non-interactive) ──────


def ensure_token(
    site_id: str,
    client_id: str | None = None,
    client_secret: str | None = None,
    redirect_uri: str | None = None,
    interactive: bool = True,
) -> TokenStore:
    """Return a TokenStore with a valid access token for the given site.

    - If a cached, non-expired token exists for this site → return it.
    - If cached but expired and a refresh token exists → refresh silently.
    - Otherwise:
        - interactive=True  → run the browser-based OAuth flow (CLI setup only).
        - interactive=False → raise RuntimeError with instructions
          (used by the running MCP server; NEVER blocks on input() during a
          live tool call, since there's no TTY attached).

    Interactive setup always uses PKCE S256 and fresh state. The complete callback
    must match the registered HTTP(S) redirect target and static query parameters.
    Hidden terminal input and a working browser are required; no URL is printed
    as a fallback. Keep PKCE enabled in the registered MercadoLibre application.
    """
    validate_site_id(site_id)
    store = TokenStore(site_id)

    # 1. Cached and valid → done.
    if store.has_token() and not store.is_expired():
        logger.info("Using cached token for %s (user_id=%s)", site_id, store.get_user_id())
        return store

    _client_id = client_id or os.environ.get("MERCADOLIBRE_CLIENT_ID", "")
    _client_secret = client_secret or os.environ.get("MERCADOLIBRE_CLIENT_SECRET", "")

    # 2. Cached but expired → try refreshing (works in both interactive and server modes).
    if store.has_token() and store.is_expired():
        refresh_token = store.get_refresh_token()
        if refresh_token:
            logger.info("Token expired for %s, attempting refresh...", site_id)
            try:
                new_data = _refresh_access_token(_client_id, _client_secret, refresh_token)
                store.update(new_data)
                logger.info("Token refreshed for %s", site_id)
                return store
            except httpx.HTTPStatusError as exc:
                logger.warning("Refresh failed for %s (%s), will re-authenticate", site_id, exc)
                store.clear()
        else:
            logger.warning("No refresh token available for %s, re-authenticating...", site_id)
            store.clear()

    # 3. No valid/refreshable token.
    if not interactive:
        raise RuntimeError(
            f"No MercadoLibre token cached for site '{site_id}' ({SITE_NAMES.get(site_id, site_id)}). "
            f"Run this once to authorize: uv run python -m mercadolibre_mcp.auth --site-id {site_id}"
        )

    if not _client_id or not _client_secret:
        raise RuntimeError(
            "MercadoLibre credentials not configured. "
            "Set MERCADOLIBRE_CLIENT_ID and MERCADOLIBRE_CLIENT_SECRET environment variables, "
            "or copy .env.example to .env and fill in your credentials."
        )

    _redirect_uri = redirect_uri or os.environ.get(
        "MERCADOLIBRE_REDIRECT_URI", "http://localhost:8080/callback"
    )
    _validate_redirect_uri(_redirect_uri)
    code_verifier = _generate_pkce_verifier()
    state = secrets.token_urlsafe(32)

    print(f"\n{'=' * 60}")
    print(f"  MercadoLibre MCP — OAuth Setup ({SITE_NAMES.get(site_id, site_id)})")
    print(f"{'=' * 60}")
    print("\n1. Opening browser to authorize with Mercado Libre...")
    print(f"   Site: {site_id} - {SITE_NAMES.get(site_id, site_id)}")
    print("\n2. Log in (if needed) and click 'Allow'.")
    print("\n3. After authorizing, you'll be redirected to a URL.")
    print("   Copy the ENTIRE redirected URL and paste it here.\n")

    auth_url = _build_authorization_url(
        _client_id, _redirect_uri, site_id, _pkce_challenge(code_verifier), state
    )
    try:
        opened = _open_authorization_browser(auth_url)
    except Exception:
        # Browser launchers may include the secret-bearing URL in their errors.
        opened = False
    if not opened:
        raise RuntimeError(
            "Could not open the authorization browser. Configure a local default browser "
            "and rerun setup in a private terminal; the authorization URL is not displayed."
        )

    code = _validate_callback(_read_callback_url(), _redirect_uri, state)
    try:
        token_data = _exchange_code_for_token(
            _client_id, _client_secret, code, _redirect_uri, code_verifier
        )
    except (httpx.HTTPError, ValueError):
        raise RuntimeError("OAuth token exchange failed. Restart setup and authorize again.") from None
    store.update(token_data)

    print("\n✓ Authentication successful!")
    print(f"  Site: {site_id} - {SITE_NAMES.get(site_id, site_id)}")
    print(f"  User ID: {store.get_user_id()}")
    print(f"  Profile saved to: {store._path}")
    print("  You can now use the MCP server for this country.")
    print(
        f"  To add another country, run: uv run python -m mercadolibre_mcp.auth --site-id <OTHER_SITE>\n"
    )

    return store


# ── CLI entry point for setup ────────────────────────────────


def run_setup() -> None:
    """CLI entry: python -m mercadolibre_mcp.auth [--site-id MLA] [--list]

    Run once per country you want to operate in. The same MERCADOLIBRE_CLIENT_ID /
    MERCADOLIBRE_CLIENT_SECRET is reused across all countries — only the token
    produced by this flow is site-specific.
    """
    import argparse

    parser = argparse.ArgumentParser(description="MercadoLibre MCP — OAuth Setup (per-country)")
    parser.add_argument(
        "--site-id",
        default=os.environ.get("MERCADOLIBRE_SITE_ID", "MLA"),
        help="MercadoLibre site ID to authorize (default: MLA). Run once per country.",
    )
    parser.add_argument(
        "--client-id",
        default=os.environ.get("MERCADOLIBRE_CLIENT_ID", ""),
        help="App client ID (or set MERCADOLIBRE_CLIENT_ID env var)",
    )
    parser.add_argument(
        "--client-secret",
        default=os.environ.get("MERCADOLIBRE_CLIENT_SECRET", ""),
        help="App client secret (or set MERCADOLIBRE_CLIENT_SECRET env var)",
    )
    parser.add_argument(
        "--redirect-uri",
        default=os.environ.get("MERCADOLIBRE_REDIRECT_URI", "http://localhost:8080/callback"),
        help="OAuth redirect URI (default: http://localhost:8080/callback)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all cached country profiles (site, user id, expiration) and exit",
    )
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

    if args.list:
        sites = list_cached_sites()
        if not sites:
            print(
                "No cached MercadoLibre profiles found. Run this command with --site-id to add one."
            )
            return
        print(f"\n{'Site':<6} {'Country':<20} {'User ID':<14} {'Status'}")
        print("-" * 60)
        for s in sites:
            status = "expired (auto-refreshes on use)" if s["is_expired"] else "valid"
            print(f"{s['site_id']:<6} {s['site_name']:<20} {str(s['user_id']):<14} {status}")
        print()
        return

    ensure_token(
        site_id=args.site_id,
        client_id=args.client_id,
        client_secret=args.client_secret,
        redirect_uri=args.redirect_uri,
        interactive=True,
    )


if __name__ == "__main__":
    run_setup()
