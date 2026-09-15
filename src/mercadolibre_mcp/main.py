# MercadoLibre MCP Server - main.py
"""FastMCP server entrypoint for the MercadoLibre REST API.

Exposes MercadoLibre's full API surface as MCP tools, organized by domain:
  - Items: search, get, create, update, delete, relist
  - Categories: list, get, predict
  - Orders: search, get, feedback
  - Users: profile, reputation
  - Shipping: methods, options, tracking
  - Questions & Answers
  - Mercado Ads campaigns
  - Metrics & Trends
  - Auth: list which countries have a cached, ready-to-use OAuth profile

Multi-country: MercadoLibre seller accounts are typically per-country, so each
site (MLA, MLU, MLB, ...) has its own cached OAuth token. Run
`uv run python -m mercadolibre_mcp.auth --site-id MLA` once per country you
operate in (see README for details). Every tool accepts an optional `site_id`
to pick which cached profile executes the call; it defaults to the
MERCADOLIBRE_SITE_ID env var (or MLA).

Run:  uv run python -m mercadolibre_mcp.main
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from mercadolibre_mcp import __version__
from mercadolibre_mcp.auth import ALL_SITE_IDS, SITE_NAMES, list_cached_sites
from mercadolibre_mcp.client import MercadoLibreClient, MercadoLibreError

logger = logging.getLogger(__name__)

# ── Shared HTTP clients — one per site/country, created lazily ─────────────

_clients: dict[tuple[str, str | None], MercadoLibreClient] = {}


def get_client(site_id: str | None = None, account: str | None = None) -> MercadoLibreClient:
    """Return a ready, token-fresh client for the given (or default) site + account.

    `account` is an optional alias selecting an additional seller account in the
    same country (e.g. 'personal', 'business'); omit it for the site's default
    account. Raises RuntimeError with a clear message if that site/account hasn't
    been authorized yet — callers should catch this alongside MercadoLibreError.
    """
    site = MercadoLibreClient.resolve_site_id(site_id)
    acct = MercadoLibreClient.resolve_account(account)
    key = (site, acct)
    client = _clients.get(key)
    if client is None:
        client = MercadoLibreClient.create(site_id=site, account=acct)
        _clients[key] = client
    client.ensure_fresh_token()
    return client


# Tool functions catch these two together — RuntimeError means "site not
# authorized yet", MercadoLibreError means "API call failed".
_CLIENT_ERRORS = (MercadoLibreError, RuntimeError)


# ── Pydantic models ─────────────────────────────────────────────────────────


class SiteParam(BaseModel):
    site_id: str | None = Field(
        default=None,
        description="MercadoLibre site ID selecting which authenticated country profile to use "
        "(e.g., 'MLA'=Argentina, 'MLU'=Uruguay, 'MLB'=Brasil). Defaults to MERCADOLIBRE_SITE_ID "
        "env var or MLA. Use list_authenticated_sites to see which ones are ready.",
    )
    account: str | None = Field(
        default=None,
        description="Optional account alias selecting an additional seller account authorized "
        "for the same country (e.g. 'personal', 'business'). Defaults to MERCADOLIBRE_ACCOUNT "
        "env var or the site's default account. Use list_authenticated_sites to see aliases.",
    )


def _resolve(input: SiteParam) -> tuple[str, str | None]:
    """Resolve the effective (site_id, account) for a tool call."""
    return (
        MercadoLibreClient.resolve_site_id(input.site_id),
        MercadoLibreClient.resolve_account(input.account),
    )


# ── Items ───────────────────────────────────────────────────────────────────


class SearchItemsInput(SiteParam):
    query: str = Field(description="Search query (e.g., 'iPhone 15', 'zapatillas running')")
    category_id: str | None = Field(default=None, description="Filter by category ID")
    price_min: float | None = Field(default=None, description="Minimum price")
    price_max: float | None = Field(default=None, description="Maximum price")
    condition: str | None = Field(default=None, description="'new' or 'used'")
    shipping_free: bool | None = Field(default=None, description="Free shipping only")
    limit: int = Field(default=20, ge=1, le=100, description="Results per page (1-100)")
    offset: int | None = Field(default=None, description="Pagination offset")


class GetItemInput(SiteParam):
    item_id: str = Field(description="MercadoLibre item ID (e.g., 'MLA1234567890')")


class CreateItemInput(SiteParam):
    title: str = Field(description="Product title (5-60 chars)")
    category_id: str = Field(description="MercadoLibre category ID")
    price: float = Field(description="Price in local currency")
    currency_id: str = Field(default="ARS", description="Currency code (ARS, UYU, BRL, etc.)")
    available_quantity: int = Field(ge=1, description="Initial stock quantity")
    condition: str = Field(default="new", description="'new' or 'used'")
    description: str | None = Field(default=None, description="Product description (supports HTML)")
    listing_type_id: str | None = Field(
        default=None, description="'gold_pro', 'gold_special', 'gold_premium', 'free', etc."
    )
    pictures_urls: list[str] | None = Field(default=None, description="List of image URLs (max 12)")
    tags: list[str] | None = Field(
        default=None, description="Tags like 'instant_payment', 'pet_side_promotion'"
    )
    attributes: list[dict[str, str]] | None = Field(
        default=None, description="Item attributes, e.g. [{\"id\": \"BRAND\", \"value_name\": \"Ubiquiti\"}]"
    )
    family_name: str | None = Field(default=None, description="Product family name")


class UpdateItemInput(SiteParam):
    item_id: str = Field(description="MercadoLibre item ID to update")
    title: str | None = Field(default=None, description="New title")
    price: float | None = Field(default=None, description="New price")
    available_quantity: int | None = Field(default=None, description="New stock quantity")
    description: str | None = Field(default=None, description="New description")
    pictures_urls: list[str] | None = Field(default=None, description="New image URLs")


class DeleteItemInput(SiteParam):
    item_id: str = Field(description="MercadoLibre item ID to close/delete")


class ListMyItemsInput(SiteParam):
    status: str | None = Field(
        default=None, description="Filter by status: 'active', 'paused', 'closed', 'pending'"
    )
    limit: int = Field(default=50, ge=1, le=200, description="Results per page")
    offset: int | None = Field(default=None, description="Pagination offset")


class RelistItemInput(SiteParam):
    item_id: str = Field(description="Item ID to relist")
    quantity: int | None = Field(default=None, description="New quantity (defaults to previous)")


# ── Categories ──────────────────────────────────────────────────────────────


class ListCategoriesInput(SiteParam):
    pass


class GetCategoryInput(SiteParam):
    category_id: str = Field(description="Category ID (e.g., 'MLA1000')")


class PredictCategoryInput(SiteParam):
    title: str = Field(description="Product title to predict category for")


# ── Orders ──────────────────────────────────────────────────────────────────


class SearchOrdersInput(SiteParam):
    status: str | None = Field(
        default=None, description="Filter: 'paid', 'shipped', 'delivered', 'cancelled'"
    )
    limit: int = Field(default=50, ge=1, le=200, description="Results per page")
    offset: int | None = Field(default=None, description="Pagination offset")


class GetOrderInput(SiteParam):
    order_id: int = Field(description="MercadoLibre order ID")


# ── Shipping ────────────────────────────────────────────────────────────────


class GetShippingMethodsInput(SiteParam):
    item_id: str = Field(description="Item ID to get shipping methods for")
    zip_code: str = Field(description="Destination zip/postal code")


class GetShipmentInput(SiteParam):
    shipment_id: int = Field(description="MercadoLibre shipment ID")


# ── Questions ───────────────────────────────────────────────────────────────


class ListQuestionsInput(SiteParam):
    item_id: str | None = Field(default=None, description="Filter by item ID")
    status: str | None = Field(default=None, description="'ANSWERED' or 'UNANSWERED'")
    limit: int = Field(default=50, ge=1, le=200)


class AnswerQuestionInput(SiteParam):
    question_id: int = Field(description="Question ID to answer")
    answer_text: str = Field(description="Answer text")


# ── Mercado Ads ─────────────────────────────────────────────────────────────


class ListCampaignsInput(SiteParam):
    status: str | None = Field(default=None, description="Filter: 'active', 'paused', 'finished'")
    limit: int = Field(default=20, ge=1, le=50)


# ── Users ───────────────────────────────────────────────────────────────────


class GetUserInput(SiteParam):
    user_id: int | None = Field(
        default=None, description="User ID. Leave empty for the authenticated user."
    )


# ── Metrics ─────────────────────────────────────────────────────────────────


class GetItemVisitsInput(SiteParam):
    item_id: str = Field(description="Item ID to get visit stats for")
    last_week: bool | None = Field(
        default=None, description="If true, returns last 7 days instead of last 24h"
    )


# ── Auth / profiles ──────────────────────────────────────────────────────────


class ListAuthenticatedSitesInput(BaseModel):
    pass


# ══════════════════════════════════════════════════════════════════════════════
# Tools
# ══════════════════════════════════════════════════════════════════════════════


def _item_summary(data: dict) -> dict:
    """Normalize an item response to a clean summary dict."""
    return {
        "id": data.get("id"),
        "title": data.get("title"),
        "price": data.get("price"),
        "currency_id": data.get("currency_id"),
        "available_quantity": data.get("available_quantity"),
        "condition": data.get("condition"),
        "status": data.get("status"),
        "listing_type_id": data.get("listing_type_id"),
        "category_id": data.get("category_id"),
        "seller_id": data.get("seller_id"),
        "permalink": data.get("permalink"),
        "thumbnail": data.get("thumbnail"),
        "tags": data.get("tags", []),
    }


# ── Items ───────────────────────────────────────────────────────────────────


async def search_items(input: SearchItemsInput) -> dict:
    """Search for products on MercadoLibre across a site (public data — any
    authenticated profile can read it, regardless of which country it belongs to).

    **Usage examples:**
      - "Find iPhone 15 in Argentina" → query="iPhone 15", site_id="MLA"
      - "Search for zapatillas running under 5000 pesos in Uruguay" →
        query="zapatillas running", price_max=5000, site_id="MLU"
      - "Find laptops in Brazil" → query="notebook", site_id="MLB"
    """
    try:
        site, account = _resolve(input)
        params = {
            "q": input.query,
            "limit": min(input.limit, 100),
        }
        if input.category_id:
            params["category"] = input.category_id
        if input.price_min is not None:
            params["price_min"] = input.price_min
        if input.price_max is not None:
            params["price_max"] = input.price_max
        if input.condition:
            params["condition"] = input.condition
        if input.shipping_free:
            params["shipping_cost"] = "free"
        if input.offset is not None:
            params["offset"] = input.offset

        data = get_client(site, account).get(f"sites/{site}/search", params=params)
        results = [_item_summary(r) for r in data.get("results", [])]
        return {
            "site_id": site,
            "query": input.query,
            "total": data.get("paging", {}).get("total", 0),
            "results": results,
            "available_filters": data.get("available_filters", []),
        }
    except MercadoLibreError as e:
        response = {"error": str(e)}
        if isinstance(e.body, dict):
            safe_keys = {"error", "message", "code", "field", "references", "cause"}
            details = {key: e.body[key] for key in safe_keys if key in e.body}
            if details:
                response["details"] = details
        return response
    except RuntimeError as e:
        return {"error": str(e)}


async def get_item(input: GetItemInput) -> dict:
    """Get full details of a MercadoLibre listing by its item ID.

    `site_id` selects which authenticated profile executes the call — since
    item details are public catalog data, any authenticated country profile
    can read an item regardless of which country it was published in.

    **Usage examples:**
      - "Get details for MLA1234567890"
      - "Show me the listing information for MLB987654321"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(f"items/{input.item_id}")
        return _item_summary(data) | {
            "warranty": data.get("warranty"),
            "listing_type_id": data.get("listing_type_id"),
            "buying_mode": data.get("buying_mode"),
            "start_time": data.get("start_time"),
            "stop_time": data.get("stop_time"),
            "sold_quantity": data.get("sold_quantity"),
            "descriptions": data.get("descriptions", []),
            "attributes": data.get("attributes", []),
            "pictures": [p.get("url") for p in data.get("pictures", [])],
            "seller_address": data.get("seller_address"),
            "shipping": data.get("shipping"),
        }
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def create_item(input: CreateItemInput) -> dict:
    """Create a new product listing on MercadoLibre.

    **Requires OAuth2 write scope** on the profile matching `site_id` — that
    profile's authenticated account is the one that will own the new listing.
    Use predict_category/list_categories first to find valid category IDs.

    **Usage examples:**
      - "Create a listing for an iPhone 15 at 1500 ARS in category MLA1051" → site_id="MLA"
      - "Publish a new running shoes product in Uruguay for 2500 UYU" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        body = {
            "title": input.title,
            "category_id": input.category_id,
            "price": input.price,
            "currency_id": input.currency_id,
            "available_quantity": input.available_quantity,
            "condition": input.condition,
            "buying_mode": "buy_it_now",
            "listing_type_id": input.listing_type_id or "free",
        }
        if input.description:
            body["description"] = {"plain_text": input.description}
        if input.pictures_urls:
            body["pictures"] = [{"source": url} for url in input.pictures_urls]
        if input.tags:
            body["tags"] = input.tags
        if input.attributes:
            body["attributes"] = input.attributes
        if input.family_name:
            body["family_name"] = input.family_name
        data = get_client(site, account).post("items", json_body=body)
        return {
            "success": True,
            "site_id": site,
            "item_id": data.get("id"),
            "permalink": data.get("permalink"),
        }
    except MercadoLibreError as e:
        response = {"error": str(e)}
        if isinstance(e.body, dict):
            safe_keys = {"error", "message", "code", "field", "references", "cause"}
            details = {key: e.body[key] for key in safe_keys if key in e.body}
            if details:
                response["details"] = details
        return response
    except RuntimeError as e:
        return {"error": str(e)}


async def update_item(input: UpdateItemInput) -> dict:
    """Update an existing MercadoLibre listing. Only provided fields are changed.

    **Important:** `site_id` must match the country whose authenticated
    profile actually OWNS this item, or the API will reject the write (403).

    **Usage examples:**
      - "Update the price of MLA1234567890 to 2000" → site_id="MLA"
      - "Change stock of item MLU987654321 to 50 units" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        body: dict = {}
        if input.title is not None:
            body["title"] = input.title
        if input.price is not None:
            body["price"] = input.price
        if input.available_quantity is not None:
            body["available_quantity"] = input.available_quantity
        if input.description is not None:
            body["description"] = {"plain_text": input.description}
        if input.pictures_urls is not None:
            body["pictures"] = [{"source": url} for url in input.pictures_urls]

        if not body:
            return {"error": "No fields to update provided"}

        data = get_client(site, account).put(f"items/{input.item_id}", json_body=body)
        return {"success": True, "item_id": data.get("id"), "status": data.get("status")}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def delete_item(input: DeleteItemInput) -> dict:
    """Close/finish a MercadoLibre listing. Sets status to 'closed'. Items can be relisted later.

    **Important:** `site_id` must match the country whose authenticated
    profile actually OWNS this item, or the API will reject the write (403).

    **Usage examples:**
      - "Remove listing MLA1234567890" → site_id="MLA"
      - "Finish my ad for MLU987654321" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).put(
            f"items/{input.item_id}", json_body={"status": "closed"}
        )
        return {"success": True, "item_id": input.item_id, "status": data.get("status")}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def list_my_items(input: ListMyItemsInput) -> dict:
    """List all items owned by the authenticated user for a given country profile.

    **Usage examples:**
      - "Show all my active listings in Argentina" → site_id="MLA"
      - "List my paused items in Uruguay" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        client = get_client(site, account)
        user_id = client.get_user_id()
        if not user_id:
            return {"error": f"Cannot determine user ID for site '{site}'. Re-run OAuth setup."}

        params = {
            "limit": min(input.limit, 200),
        }
        if input.status:
            params["status"] = input.status
        if input.offset is not None:
            params["offset"] = input.offset

        data = client.get(f"users/{user_id}/items/search", params=params)
        results = data.get("results", [])
        # If results contain item IDs, fetch each one's summary
        if results and isinstance(results[0], str):
            detailed = []
            for item_id in results[:20]:  # limit concurrent fetches
                try:
                    detailed.append(_item_summary(client.get(f"items/{item_id}")))
                except Exception:
                    detailed.append({"id": item_id})
            results = detailed

        return {
            "site_id": site,
            "total": data.get("paging", {}).get("total", 0),
            "results": results,
        }
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def relist_item(input: RelistItemInput) -> dict:
    """Relist a previously closed item as a new listing.

    **Usage examples:**
      - "Relist MLA1234567890" → site_id="MLA"
      - "Republish my finished ad with 10 units available" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        body: dict = {}
        if input.quantity is not None:
            body["available_quantity"] = input.quantity
        data = get_client(site, account).put(f"items/{input.item_id}/relist", json_body=body)
        return {"success": True, "new_item_id": data.get("id"), "permalink": data.get("permalink")}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Categories ──────────────────────────────────────────────────────────────


async def list_categories(input: ListCategoriesInput) -> dict:
    """Get all top-level categories available on a MercadoLibre site.

    **Usage examples:**
      - "What categories are available in Argentina?" → site_id="MLA"
      - "List categories for Uruguay" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(f"sites/{site}/categories")
        return {"site_id": site, "categories": data}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def get_category(input: GetCategoryInput) -> dict:
    """Get detailed information about a specific category, including its children and attributes.

    **Usage examples:**
      - "Tell me about category MLA1051"
      - "What are the attributes for category MLU1000?"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(f"categories/{input.category_id}")
        return data
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def predict_category(input: PredictCategoryInput) -> dict:
    """Predict the best matching MercadoLibre category for a given product title.

    **Usage examples:**
      - "Which category should I use for 'iPhone 15 Pro Max 256GB'?" → site_id="MLA"
      - "Predict category for 'Zapatillas Nike Running Hombre'" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(
            f"sites/{site}/domain_discovery/search",
            params={"q": input.title},
        )
        return {"site_id": site, "predictions": data}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Orders ──────────────────────────────────────────────────────────────────


async def search_orders(input: SearchOrdersInput) -> dict:
    """Search orders for the authenticated seller in a given country.

    **Requires OAuth2 with read scope.**

    **Usage examples:**
      - "Show me my recent orders in Argentina" → site_id="MLA"
      - "List my paid orders in Uruguay" → site_id="MLU"
      - "Get orders that are pending shipment"
    """
    try:
        site, account = _resolve(input)
        client = get_client(site, account)
        user_id = client.get_user_id()
        if not user_id:
            return {"error": f"Cannot determine user ID for site '{site}'. Re-run OAuth setup."}

        params = {"seller": user_id, "limit": min(input.limit, 200)}
        if input.status:
            params["order.status"] = input.status
        if input.offset is not None:
            params["offset"] = input.offset

        data = client.get("orders/search", params=params)
        return {
            "site_id": site,
            "total": data.get("paging", {}).get("total", 0),
            "results": data.get("results", []),
        }
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def get_order(input: GetOrderInput) -> dict:
    """Get detailed information about a specific order.

    **Usage examples:**
      - "Show me details for order 1234567890" → site_id="MLA" (or wherever the order lives)
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(f"orders/{input.order_id}")
        return data
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Shipping ────────────────────────────────────────────────────────────────


async def get_shipping_methods(input: GetShippingMethodsInput) -> dict:
    """Get available shipping methods for an item to a destination zip code.

    **Usage examples:**
      - "What shipping options are available for item MLA123 to zip 11000?"
    """
    try:
        site, account = _resolve(input)
        client = get_client(site, account)
        item_data = client.get(f"items/{input.item_id}")
        data = client.get(
            f"sites/{site}/shipping_methods",
            params={
                "item_id": input.item_id,
                "zip_code": input.zip_code,
                "dimensions": item_data.get("dimensions", ""),
            },
        )
        return {"methods": data}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def get_shipment(input: GetShipmentInput) -> dict:
    """Get tracking information for a shipment.

    **Usage examples:**
      - "Track shipment 987654321"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).get(f"shipments/{input.shipment_id}")
        return {
            "id": data.get("id"),
            "status": data.get("status"),
            "tracking_number": data.get("tracking_number"),
            "substatus": data.get("substatus"),
            "date_created": data.get("date_created"),
            "last_updated": data.get("last_updated"),
            "receiver_address": data.get("receiver_address"),
            "sender_address": data.get("sender_address"),
            "estimated_delivery": data.get("estimated_delivery"),
        }
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Questions ───────────────────────────────────────────────────────────────


async def list_questions(input: ListQuestionsInput) -> dict:
    """List questions asked about items, optionally filtered by item or status.

    **Usage examples:**
      - "Show me unanswered questions for my items" → site_id="MLA"
      - "List questions about item MLU1234567890"
    """
    try:
        site, account = _resolve(input)
        params: dict = {}
        if input.item_id:
            params["item_id"] = input.item_id
        if input.status:
            params["status"] = input.status
        params["limit"] = min(input.limit, 200)
        data = get_client(site, account).get("questions/search", params=params)
        return {"results": data.get("questions", data.get("results", []))}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


async def answer_question(input: AnswerQuestionInput) -> dict:
    """Answer a pending question about an item.

    **Usage examples:**
      - "Answer question 98765 with 'Yes, we have stock'"
    """
    try:
        site, account = _resolve(input)
        data = get_client(site, account).post(
            "answers",
            json_body={"question_id": input.question_id, "text": input.answer_text},
        )
        return {"success": True, "answer_id": data.get("id")}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Users ───────────────────────────────────────────────────────────────────


async def get_user(input: GetUserInput) -> dict:
    """Get public profile and seller reputation for a MercadoLibre user.

    Leave user_id empty to get the authenticated user's own profile for `site_id`.

    **Usage examples:**
      - "What's my MercadoLibre profile in Argentina?" → site_id="MLA"
      - "Show seller reputation for user 123456789"
    """
    try:
        site, account = _resolve(input)
        client = get_client(site, account)
        if input.user_id:
            data = client.get(f"users/{input.user_id}")
        else:
            data = client.get("users/me")
        return {
            "id": data.get("id"),
            "nickname": data.get("nickname"),
            "first_name": data.get("first_name"),
            "last_name": data.get("last_name"),
            "email": data.get("email"),
            "points": data.get("points"),
            "seller_reputation": data.get("seller_reputation"),
            "buyer_reputation": data.get("buyer_reputation"),
            "site_id": data.get("site_id"),
            "status": data.get("status"),
        }
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Mercado Ads ─────────────────────────────────────────────────────────────


async def list_ads_campaigns(input: ListCampaignsInput) -> dict:
    """List Mercado Ads campaigns for the authenticated seller in a given country.

    **Requires OAuth2. Mercado Ads must be enabled for the account.**

    **Usage examples:**
      - "Show my active advertising campaigns" → site_id="MLA"
      - "List my Mercado Ads campaigns in Uruguay" → site_id="MLU"
    """
    try:
        site, account = _resolve(input)
        client = get_client(site, account)
        user_id = client.get_user_id()
        if not user_id:
            return {"error": f"Cannot determine user ID for site '{site}'. Re-run OAuth setup."}
        params = {"seller_id": user_id, "limit": min(input.limit, 50)}
        if input.status:
            params["status"] = input.status
        data = client.get("advertising/campaigns", params=params)
        return {"site_id": site, "campaigns": data.get("results", data.get("campaigns", data))}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Metrics ─────────────────────────────────────────────────────────────────


async def get_item_visits(input: GetItemVisitsInput) -> dict:
    """Get visit statistics for a MercadoLibre listing.

    **Usage examples:**
      - "How many views does MLA1234567890 have?"
      - "Show me last week's visits for item MLU987654321" → last_week=true
    """
    try:
        site, account = _resolve(input)
        path = f"items/{input.item_id}/visits"
        if input.last_week:
            path += "?last_week=true"
        data = get_client(site, account).get(path)
        return {"item_id": input.item_id, "visits": data}
    except _CLIENT_ERRORS as e:
        return {"error": str(e)}


# ── Auth / profiles ──────────────────────────────────────────────────────────


async def list_authenticated_sites(input: ListAuthenticatedSitesInput) -> dict:
    """List which MercadoLibre country profiles are authenticated and ready to use,
    and which of the 18 supported sites are NOT yet authenticated.

    This does not create any client or make any API call — it only inspects
    locally cached OAuth profiles under ~/.mercadolibre_mcp/profiles/.

    **Usage examples:**
      - "Which countries am I authenticated in?"
      - "Show me my connected MercadoLibre accounts"
      - "Am I set up for Uruguay yet?"
    """
    cached = list_cached_sites()
    cached_ids = {c["site_id"] for c in cached}
    not_authenticated = [
        {"site_id": s, "site_name": SITE_NAMES.get(s, s)}
        for s in ALL_SITE_IDS
        if s not in cached_ids
    ]
    return {
        "authenticated": cached,
        "not_authenticated": not_authenticated,
        "hint": "Run `uv run python -m mercadolibre_mcp.auth --site-id <SITE_ID>` to add a "
        "country, or add `--account <alias>` to authorize an additional account for a "
        "country you already use.",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Server
# ══════════════════════════════════════════════════════════════════════════════


@asynccontextmanager
async def server_lifespan(server: FastMCP) -> AsyncIterator[None]:
    """FastMCP lifespan — runs once at startup and shutdown."""
    logger.info("Starting MercadoLibre MCP Server")
    cached = list_cached_sites()
    if cached:
        sites = ", ".join(
            f"{c['site_id']}"
            + (f"/{c['account']}" if c.get("account") else "")
            + f" ({c['site_name']})"
            for c in cached
        )
        logger.info("Authenticated profiles: %s", sites)
    else:
        logger.warning(
            "No authenticated profiles found. Run: "
            "uv run python -m mercadolibre_mcp.auth --site-id <SITE_ID>"
        )
    yield
    logger.info("Shutting down MercadoLibre MCP Server")


# Create the FastMCP server
mcp = FastMCP(
    "MercadoLibre API",
    version=__version__,
    instructions="MCP server for MercadoLibre's REST API — manage listings, orders, shipping, ads, "
    "and more across 18 countries. Every tool accepts an optional site_id (MLA=Argentina, "
    "MLU=Uruguay, MLB=Brasil, etc.) to select which authenticated country profile executes the "
    "call; it defaults to MERCADOLIBRE_SITE_ID. Tools also accept an optional account alias to "
    "select an additional seller account authorized for the same country (run the auth CLI with "
    "--account <alias>); it defaults to MERCADOLIBRE_ACCOUNT or the site's default account. "
    "Each country/account requires its own one-time OAuth authorization since MercadoLibre "
    "seller accounts are typically per-country. Use list_authenticated_sites to see what's ready. "
    "Credentials are loaded from environment variables, never exposed to the LLM.",
    lifespan=server_lifespan,
)

# ── register tools ────────────────────────────────────────────

mcp.tool(search_items, name="search_items", tags={"items", "search", "products"})
mcp.tool(get_item, name="get_item", tags={"items", "products"})
mcp.tool(create_item, name="create_item", tags={"items", "listings", "create"})
mcp.tool(update_item, name="update_item", tags={"items", "listings", "update"})
mcp.tool(delete_item, name="delete_item", tags={"items", "listings", "delete"})
mcp.tool(list_my_items, name="list_my_items", tags={"items", "listings", "my-account"})
mcp.tool(relist_item, name="relist_item", tags={"items", "listings", "relist"})
mcp.tool(list_categories, name="list_categories", tags={"categories", "search"})
mcp.tool(get_category, name="get_category", tags={"categories"})
mcp.tool(predict_category, name="predict_category", tags={"categories", "search"})
mcp.tool(search_orders, name="search_orders", tags={"orders", "sales"})
mcp.tool(get_order, name="get_order", tags={"orders", "sales"})
mcp.tool(get_shipping_methods, name="get_shipping_methods", tags={"shipping", "logistics"})
mcp.tool(get_shipment, name="get_shipment", tags={"shipping", "logistics", "tracking"})
mcp.tool(list_questions, name="list_questions", tags={"questions", "support"})
mcp.tool(answer_question, name="answer_question", tags={"questions", "support"})
mcp.tool(get_user, name="get_user", tags={"users", "profile"})
mcp.tool(list_ads_campaigns, name="list_ads_campaigns", tags={"ads", "advertising", "campaigns"})
mcp.tool(get_item_visits, name="get_item_visits", tags={"metrics", "analytics", "visits"})
mcp.tool(
    list_authenticated_sites,
    name="list_authenticated_sites",
    tags={"auth", "profiles", "meta", "countries"},
)


# ── tool search (BM25 transform) ────────────────────────────────────────────

# Replace the flat tool list with a BM25 search so the LLM can discover
# tools by searching keywords rather than enumerating all 20+.
try:
    from fastmcp.server.transforms.search import BM25Search

    mcp.add_transform(BM25Search(description="Search MercadoLibre tools by name or description"))
except (ImportError, AttributeError):
    pass  # BM25 not available in older FastMCP versions


# ── CLI entry ───────────────────────────────────────────────────────────────


def run() -> None:
    """Entry point for `mercadolibre-mcp` CLI command.

    `mercadolibre-mcp --version` prints the version and exits without starting
    the server, so you can confirm which build you are running.
    """
    if "--version" in sys.argv[1:]:
        print(f"mercadolibre-mcp {__version__}")
        return
    mcp.run(transport="stdio")


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    run()
