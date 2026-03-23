"""
Test per business logic di shopify-mysql-sync.
Copre: filtro tag, sanitizzazione HTML, estrazione metafields,
normalizzazione prodotti GraphQL, costruzione JSON immagini.
"""

import json
import pytest

from shopify_to_mysql import is_shoe, sanitize_html
from src.config import VALID_TAGS
from src.shopify_client import ShopifyClient


# --- is_shoe ---

class TestIsShoe:
    def test_matching_tag(self):
        product = {"tags": "sneakers personalizzate, outlet"}
        assert is_shoe(product, VALID_TAGS) is True

    def test_matching_tag_case_insensitive(self):
        product = {"tags": "Sneakers Personalizzate"}
        assert is_shoe(product, VALID_TAGS) is True

    def test_multiple_valid_tags(self):
        product = {"tags": "scarpe personalizzate, stivali personalizzati"}
        assert is_shoe(product, VALID_TAGS) is True

    def test_no_matching_tag(self):
        product = {"tags": "accessori, borse"}
        assert is_shoe(product, VALID_TAGS) is False

    def test_empty_tags(self):
        product = {"tags": ""}
        assert is_shoe(product, VALID_TAGS) is False

    def test_missing_tags_key(self):
        product = {}
        assert is_shoe(product, VALID_TAGS) is False

    def test_partial_match_not_valid(self):
        product = {"tags": "sneakers"}
        assert is_shoe(product, VALID_TAGS) is False

    def test_all_valid_tags(self):
        for tag in VALID_TAGS:
            product = {"tags": tag}
            assert is_shoe(product, VALID_TAGS) is True, f"Tag '{tag}' should match"

    def test_graphql_comma_separated_tags(self):
        """GraphQL restituisce tags come stringa comma-separated."""
        product = {"tags": "ciabatte personalizzate, estate 2025"}
        assert is_shoe(product, VALID_TAGS) is True


# --- sanitize_html ---

class TestSanitizeHtml:
    def test_none_input(self):
        assert sanitize_html(None) is None

    def test_normal_html(self):
        html = "<p>Test</p>"
        assert sanitize_html(html) == "<p>Test</p>"

    def test_bom_removal(self):
        html = "\ufeff<p>Test</p>"
        assert sanitize_html(html) == "<p>Test</p>"

    def test_byte_bom_removal(self):
        html = "\xef\xbb\xbf<p>Test</p>"
        assert sanitize_html(html) == "<p>Test</p>"

    def test_empty_string(self):
        assert sanitize_html("") == ""


# --- extract_product_metafields ---

class TestExtractProductMetafields:
    def test_basic_extraction(self):
        metafields = {
            "custom.customization_description": "Scarpe personalizzate",
            "custom.shoe_details": "Pelle italiana",
        }
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["customization_description"] == "Scarpe personalizzate"
        assert result["shoe_details"] == "Pelle italiana"

    def test_handling_int_conversion(self):
        metafields = {"custom.handling": "5"}
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["handling"] == 5

    def test_handling_invalid_value(self):
        metafields = {"custom.handling": "abc"}
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["handling"] is None

    def test_google_custom_product_true(self):
        metafields = {"mm-google-shopping.custom_product": "true"}
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["google_custom_product"] is True

    def test_google_custom_product_false(self):
        metafields = {"mm-google-shopping.custom_product": "false"}
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["google_custom_product"] is False

    def test_empty_metafields(self):
        result = ShopifyClient.extract_product_metafields({})
        assert result == {}

    def test_unknown_keys_ignored(self):
        metafields = {"unknown.key": "value"}
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result == {}

    def test_google_shopping_fields(self):
        metafields = {
            "mm-google-shopping.age_group": "adult",
            "mm-google-shopping.gender": "unisex",
            "mm-google-shopping.condition": "new",
            "mm-google-shopping.color": "nero",
            "mm-google-shopping.size": "42",
            "mm-google-shopping.material": "pelle",
            "mm-google-shopping.google_product_category": "Apparel > Shoes",
        }
        result = ShopifyClient.extract_product_metafields(metafields)
        assert result["google_age_group"] == "adult"
        assert result["google_gender"] == "unisex"
        assert result["google_condition"] == "new"
        assert result["google_color"] == "nero"
        assert result["google_size"] == "42"
        assert result["google_material"] == "pelle"
        assert result["google_product_category"] == "Apparel > Shoes"


# --- build_images_json ---

class TestBuildImagesJson:
    def test_no_images(self):
        product = {"images": []}
        result = json.loads(ShopifyClient.build_images_json(product))
        assert result["count"] == 0
        assert result["images"] == []

    def test_with_images(self):
        product = {
            "images": [
                {"id": 1, "position": 1, "src": "https://img.com/1.jpg", "alt": "Front", "width": 800, "height": 600},
                {"id": 2, "position": 2, "src": "https://img.com/2.jpg", "alt": "", "width": 800, "height": 600},
            ],
            "image": {"src": "https://img.com/1.jpg"},
        }
        result = json.loads(ShopifyClient.build_images_json(product))
        assert result["count"] == 2
        assert len(result["images"]) == 2
        assert result["images"][0]["src"] == "https://img.com/1.jpg"
        assert result["featured"] == "https://img.com/1.jpg"

    def test_no_featured_image(self):
        product = {"images": [{"id": 1, "position": 1, "src": "https://img.com/1.jpg", "alt": "", "width": 100, "height": 100}]}
        result = json.loads(ShopifyClient.build_images_json(product))
        assert "featured" not in result

    def test_alt_none_becomes_empty_string(self):
        product = {"images": [{"id": 1, "position": 1, "src": "https://img.com/1.jpg", "alt": None, "width": 100, "height": 100}]}
        result = json.loads(ShopifyClient.build_images_json(product))
        assert result["images"][0]["alt"] == ""


# --- normalize_graphql_product ---

class TestNormalizeGraphqlProduct:
    def _make_client(self):
        """Crea un client con config mockata."""
        from unittest.mock import MagicMock
        config = MagicMock()
        return ShopifyClient(config)

    def test_basic_normalization(self):
        node = {
            "legacyResourceId": "123456",
            "title": "Air Max Custom",
            "handle": "air-max-custom",
            "vendor": "Nike",
            "tags": ["sneakers personalizzate", "outlet"],
            "productType": "Shoes",
            "descriptionHtml": "<p>Descrizione</p>",
            "status": "ACTIVE",
            "featuredImage": None,
            "images": {"edges": []},
            "metafields": {"edges": []},
            "variants": {"edges": []},
        }
        client = self._make_client()
        result = client._normalize_graphql_product(node)

        assert result["id"] == 123456
        assert result["title"] == "Air Max Custom"
        assert result["handle"] == "air-max-custom"
        assert result["tags"] == "sneakers personalizzate, outlet"
        assert result["status"] == "active"
        assert result["body_html"] == "<p>Descrizione</p>"

    def test_variant_normalization(self):
        node = {
            "legacyResourceId": "100",
            "title": "Test",
            "handle": "test",
            "vendor": "V",
            "tags": [],
            "productType": "",
            "descriptionHtml": None,
            "status": "ACTIVE",
            "featuredImage": None,
            "images": {"edges": []},
            "metafields": {"edges": []},
            "variants": {
                "edges": [{
                    "node": {
                        "legacyResourceId": "200",
                        "title": "42",
                        "sku": "SKU-42",
                        "barcode": "123",
                        "price": "99.99",
                        "compareAtPrice": "129.99",
                        "inventoryItem": {
                            "legacyResourceId": "300",
                            "inventoryLevels": {"edges": []},
                        },
                    }
                }]
            },
        }
        client = self._make_client()
        result = client._normalize_graphql_product(node)

        assert len(result["variants"]) == 1
        v = result["variants"][0]
        assert v["id"] == 200
        assert v["sku"] == "SKU-42"
        assert v["price"] == "99.99"
        assert v["compare_at_price"] == "129.99"
        assert v["inventory_item_id"] == 300

    def test_stock_for_location(self):
        node = {
            "legacyResourceId": "100",
            "title": "Test",
            "handle": "test",
            "vendor": "V",
            "tags": [],
            "productType": "",
            "descriptionHtml": None,
            "status": "ACTIVE",
            "featuredImage": None,
            "images": {"edges": []},
            "metafields": {"edges": []},
            "variants": {
                "edges": [{
                    "node": {
                        "legacyResourceId": "200",
                        "title": "42",
                        "sku": "",
                        "barcode": "",
                        "price": "0",
                        "compareAtPrice": None,
                        "inventoryItem": {
                            "legacyResourceId": "300",
                            "inventoryLevels": {
                                "edges": [{
                                    "node": {
                                        "location": {"name": "Magazzino"},
                                        "quantities": [{"name": "available", "quantity": 5}],
                                    }
                                }]
                            },
                        },
                    }
                }]
            },
        }
        client = self._make_client()
        result = client._normalize_graphql_product(node, location_name="Magazzino")

        assert result["variants"][0]["stock_for_location"] == 5

    def test_stock_location_not_found(self):
        node = {
            "legacyResourceId": "100",
            "title": "Test",
            "handle": "test",
            "vendor": "V",
            "tags": [],
            "productType": "",
            "descriptionHtml": None,
            "status": "ACTIVE",
            "featuredImage": None,
            "images": {"edges": []},
            "metafields": {"edges": []},
            "variants": {
                "edges": [{
                    "node": {
                        "legacyResourceId": "200",
                        "title": "42",
                        "sku": "",
                        "barcode": "",
                        "price": "0",
                        "compareAtPrice": None,
                        "inventoryItem": {
                            "legacyResourceId": "300",
                            "inventoryLevels": {
                                "edges": [{
                                    "node": {
                                        "location": {"name": "Promo"},
                                        "quantities": [{"name": "available", "quantity": 3}],
                                    }
                                }]
                            },
                        },
                    }
                }]
            },
        }
        client = self._make_client()
        result = client._normalize_graphql_product(node, location_name="Magazzino")

        assert result["variants"][0]["stock_for_location"] is None


# --- extract_next_link ---

class TestExtractNextLink:
    def test_with_next(self):
        link = '<https://shop.myshopify.com/admin/api/products.json?page_info=abc>; rel="next"'
        result = ShopifyClient.extract_next_link(link)
        assert result == "https://shop.myshopify.com/admin/api/products.json?page_info=abc"

    def test_with_prev_only(self):
        link = '<https://shop.myshopify.com/admin/api/products.json?page_info=abc>; rel="previous"'
        result = ShopifyClient.extract_next_link(link)
        assert result is None

    def test_none(self):
        assert ShopifyClient.extract_next_link(None) is None

    def test_empty(self):
        assert ShopifyClient.extract_next_link("") is None
