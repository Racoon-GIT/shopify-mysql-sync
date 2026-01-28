#!/usr/bin/env python3
"""Test sync senza DB - verifica estrazione metafield."""

import os
os.environ["SHOPIFY_DOMAIN"] = "racoon-lab.myshopify.com"
os.environ["SHOPIFY_TOKEN"] = "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"
os.environ["DB_HOST"] = "localhost"
os.environ["DB_USER"] = "root"
os.environ["DB_PASS"] = "test"
os.environ["DB_NAME"] = "test"

from src.config import Config, log
from src.shopify_client import ShopifyClient

def main():
    config = Config.from_env(require_product_ids=False)
    client = ShopifyClient(config)

    count = 0
    for product in client.get_products_graphql(status="active", location_name="Magazzino"):
        # Filtro per tag (stesso del sync)
        tags_raw = product.get("tags", "")
        tags = [t.strip().lower() for t in tags_raw.split(",")]
        if not any(tag in config.VALID_TAGS for tag in tags):
            continue

        count += 1
        product_id = product["id"]

        # Estrai metafield prodotto
        raw_product_mf = product.get("metafields", {})
        product_mf = ShopifyClient.extract_product_metafields(raw_product_mf)

        log(f"\n{'='*60}")
        log(f"Prodotto: {product.get('title')} (ID: {product_id})")
        log(f"Metafield Google Shopping estratti:")
        for key, value in product_mf.items():
            if key.startswith("google_"):
                log(f"  {key}: {value}")

        if count >= 3:
            break

    log(f"\n{'='*60}")
    log(f"Test completato. Elaborati {count} prodotti.")

if __name__ == "__main__":
    main()
