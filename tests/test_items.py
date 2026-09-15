"""Offline tests for item update behavior."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock, patch

from mercadolibre_mcp.main import UpdateItemInput, update_item


class UpdateItemDescriptionTests(IsolatedAsyncioTestCase):
    async def test_existing_description_uses_put_api_v2(self) -> None:
        client = Mock()
        client.put.return_value = {"id": "MLU1503849984", "status": "under_review"}

        with patch("mercadolibre_mcp.main.get_client", return_value=client):
            result = await update_item(
                UpdateItemInput(
                    site_id="MLU",
                    item_id="MLU1503849984",
                    description="Descripción actualizada",
                )
            )

        self.assertEqual(
            result,
            {"success": True, "item_id": "MLU1503849984", "status": None},
        )
        client.put.assert_called_once_with(
            "items/MLU1503849984/description",
            params={"api_version": "2"},
            json_body={"plain_text": "Descripción actualizada"},
        )
        client.post.assert_not_called()
