# MercadoLibre MCP Server - client.py
"""HTTP client for the MercadoLibre REST API — one client instance per site/country.

Wraps every public and private MercadoLibre API endpoint with:
  - Automatic auth header injection (access token from the site's TokenStore)
  - Rate-limit awareness (429 retry with backoff)
  - Multi-site routing (MLA, MLU, MLB, etc.) — each site has its own cached
    OAuth profile since MercadoLibre seller accounts are typically per-country
  - Structured error handling
  - Logging
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from mercadolibre_mcp.auth import TokenStore, ensure_token, validate_site_id

logger = logging.getLogger(__name__)

API_BASE = "https://api.mercadolibre.com"

DEFAULT_TIMEOUT = 30  # seconds


class MercadoLibreError(Exception):
    """Raised when the MercadoLibre API returns a non-2xx status."""

    def __init__(self, status: int, message: str, body: Any = None) -> None:
        self.status = status
        self.body = body
        super().__init__(f"[{status}] {message}")


class MercadoLibreClient:
    """Thin sync wrapper around the MercadoLibre REST API, bound to one site/country.

    Callers obtain an instance via :meth:`create` (auto-authenticated, non-interactive —
    raises a clear error if that site hasn't been authorized yet via the CLI setup).
    """

    def __init__(
        self,
        token_store: TokenStore,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._store = token_store
        self._timeout = timeout
        self.site_id = token_store.site_id

    # ── factories ──────────────────────────────────────────────────────────

    @classmethod
    def create(cls, site_id: str) -> MercadoLibreClient:
        """Factory: ensure a valid token for `site_id` and return a ready client.

        Non-interactive: if no token is cached for this site, raises RuntimeError
        with instructions instead of blocking on a browser/input() flow (there's
        no TTY available while serving a live MCP tool call).
        """
        store = ensure_token(site_id=site_id, interactive=False)
        return cls(token_store=store)

    # ── request helpers ────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        token = self._store.get_access_token()
        if not token:
            raise RuntimeError(
                f"No access token available for site '{self.site_id}'. "
                f"Run: uv run python -m mercadolibre_mcp.auth --site-id {self.site_id}"
            )
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _make_url(self, path: str) -> str:
        """Build the full API URL. Base is always https://api.mercadolibre.com."""
        return f"{API_BASE}/{path.lstrip('/')}"

    # ── HTTP methods (sync for simplicity; httpx handles the pool) ─────────

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute an HTTP request and return parsed JSON."""
        url = self._make_url(path)
        headers = self._headers()

        logger.debug("[%s] %s %s %s", self.site_id, method, url, params or "")

        try:
            resp = httpx.request(
                method=method,
                url=url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=self._timeout,
            )
        except httpx.RequestError as exc:
            raise MercadoLibreError(0, f"Request failed: {exc}") from exc

        # Check for 429 (rate limit) and retry if possible
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "5"))
            logger.warning("Rate limited. Retrying after %ds...", retry_after)
            import time

            time.sleep(retry_after)
            return self._request(method, path, params, json_body)

        if not (200 <= resp.status_code < 300):
            try:
                error_body: dict | None = resp.json()
                message = (
                    error_body.get("message", error_body.get("error", str(resp.text)))
                    if isinstance(error_body, dict)
                    else str(resp.text)
                )
            except Exception:
                error_body = None
                message = resp.text[:500]
            raise MercadoLibreError(resp.status_code, message, body=error_body)

        try:
            return resp.json() if resp.content else {}
        except Exception:
            return {"raw": resp.text}

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, params=params, json_body=json_body)

    def put(
        self,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("PUT", path, params=params, json_body=json_body)

    def delete(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request("DELETE", path, params=params)

    # ── site helpers ───────────────────────────────────────────────────────

    @staticmethod
    def resolve_site_id(site_id: str | None) -> str:
        """Return the effective site ID from tool arg, env, or default."""
        if site_id:
            validate_site_id(site_id)
            return site_id
        return os.environ.get("MERCADOLIBRE_SITE_ID", "MLA")

    # ── token validation ───────────────────────────────────────────────────

    def ensure_fresh_token(self) -> None:
        """Refresh the access token if it's expired (idempotent, non-interactive)."""
        if self._store.is_expired():
            logger.info("Token expired for %s, refreshing...", self.site_id)
            self._store = ensure_token(
                site_id=self.site_id,
                client_id=os.environ.get("MERCADOLIBRE_CLIENT_ID"),
                client_secret=os.environ.get("MERCADOLIBRE_CLIENT_SECRET"),
                interactive=False,
            )

    def get_user_id(self) -> int | None:
        return self._store.get_user_id()
