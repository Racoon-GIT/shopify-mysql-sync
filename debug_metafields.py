#!/usr/bin/env python3
"""Debug: verifica metafield varianti via GraphQL."""

from src.config import Config, log
from src.shopify_client import ShopifyClient

def main():
    config = Config.from_env(require_product_ids=False)
    client = ShopifyClient(config)

    # Prendi solo il primo prodotto con varianti
    count = 0
    for product in client.get_products_graphql(status="active", location_name="Magazzino"):
        count += 1
        log(f"\n{'='*60}")
        log(f"Prodotto: {product['title']} (ID: {product['id']})")
        log(f"Tags: {product.get('tags', '')}")

        # Metafield prodotto
        log(f"\nMetafield PRODOTTO ({len(product.get('metafields', {}))} trovati):")
        for key, value in product.get('metafields', {}).items():
            log(f"  {key}: {value[:50] if value else 'None'}...")

        # Varianti e loro metafield
        for variant in product.get('variants', []):
            log(f"\nVariante: {variant['title']} (ID: {variant['id']})")
            log(f"  SKU: {variant.get('sku')}")
            mf = variant.get('metafields', {})
            log(f"  Metafield VARIANTE ({len(mf)} trovati):")
            for key, value in mf.items():
                log(f"    {key}: {value[:80] if value else 'None'}...")

        # Stop dopo 2 prodotti
        if count >= 2:
            break

    log(f"\n{'='*60}")
    log("Debug completato.")

if __name__ == "__main__":
    main()
