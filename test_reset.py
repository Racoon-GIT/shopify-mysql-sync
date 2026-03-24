"""
Test per business logic di reset_variants.py.
Copre: filtro "perso", costruzione payload variante, logica backup/restore.
"""

import json
import pytest
from unittest.mock import MagicMock, patch

from reset_variants import create_variant_from_backup


class TestCreateVariantFromBackup:
    """Test per la logica di filtro e creazione variante da backup."""

    def _make_variant_json(self, **overrides):
        """Helper: crea JSON variante con valori di default."""
        variant = {
            "id": 100,
            "title": "42",
            "option1": "42",
            "option2": None,
            "option3": None,
            "price": "99.99",
            "compare_at_price": "129.99",
            "sku": "SKU-42",
            "barcode": "1234567890",
            "inventory_management": "shopify",
            "inventory_policy": "deny",
            "fulfillment_service": "manual",
            "requires_shipping": True,
            "taxable": True,
            "weight": 0.5,
            "weight_unit": "kg",
        }
        variant.update(overrides)
        return json.dumps(variant)

    def test_skip_variant_with_perso_in_title(self):
        """Varianti con 'perso' nel titolo vengono skippate."""
        client = MagicMock()
        variant_json = self._make_variant_json(title="Outlet - 42 - perso")
        result = create_variant_from_backup("12345", variant_json, client)
        assert result is None
        client.create_variant.assert_not_called()

    def test_skip_variant_perso_case_insensitive(self):
        """Il filtro 'perso' e' case-insensitive."""
        client = MagicMock()
        variant_json = self._make_variant_json(title="PERSO - 43")
        result = create_variant_from_backup("12345", variant_json, client)
        assert result is None

    def test_normal_variant_calls_create(self):
        """Varianti normali vengono create via client."""
        client = MagicMock()
        client.create_variant.return_value = {
            "id": 200,
            "inventory_item_id": 300,
        }
        variant_json = self._make_variant_json(title="42")
        result = create_variant_from_backup("12345", variant_json, client)

        assert result is not None
        assert result["inventory_item_id"] == 300
        client.create_variant.assert_called_once()
        call_args = client.create_variant.call_args
        assert call_args[0][0] == 12345  # product_id as int
        payload = call_args[0][1]
        assert payload["option1"] == "42"
        assert payload["price"] == "99.99"
        assert payload["sku"] == "SKU-42"

    def test_create_variant_failure_returns_none(self):
        """Se la creazione fallisce, ritorna None."""
        client = MagicMock()
        client.create_variant.side_effect = Exception("API error")
        variant_json = self._make_variant_json(title="42")
        result = create_variant_from_backup("12345", variant_json, client)
        assert result is None

    def test_variant_without_inventory_item_id(self):
        """Variante creata senza inventory_item_id."""
        client = MagicMock()
        client.create_variant.return_value = {"id": 200}
        variant_json = self._make_variant_json(title="42")
        result = create_variant_from_backup("12345", variant_json, client)
        assert result is not None
        assert result.get("inventory_item_id") is None

    def test_empty_title_not_filtered(self):
        """Variante con titolo vuoto NON viene filtrata."""
        client = MagicMock()
        client.create_variant.return_value = {"id": 200, "inventory_item_id": 300}
        variant_json = self._make_variant_json(title="")
        result = create_variant_from_backup("12345", variant_json, client)
        assert result is not None

    def test_payload_preserves_all_fields(self):
        """Il payload inviato a Shopify contiene tutti i campi necessari."""
        client = MagicMock()
        client.create_variant.return_value = {"id": 200, "inventory_item_id": 300}
        variant_json = self._make_variant_json(
            option1="42",
            option2="Nero",
            option3="Pelle",
            weight=0.8,
            weight_unit="kg",
        )
        create_variant_from_backup("12345", variant_json, client)

        payload = client.create_variant.call_args[0][1]
        assert payload["option1"] == "42"
        assert payload["option2"] == "Nero"
        assert payload["option3"] == "Pelle"
        assert payload["weight"] == 0.8
        assert payload["weight_unit"] == "kg"
        assert payload["inventory_management"] == "shopify"
        assert payload["inventory_policy"] == "deny"
