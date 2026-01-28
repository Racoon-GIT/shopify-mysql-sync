#!/usr/bin/env python3
"""Test diretto GraphQL per debug metafield."""

import requests
import json

SHOP_DOMAIN = "racoon-lab.myshopify.com"
ACCESS_TOKEN = "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"
API_VERSION = "2024-04"

# Query per un prodotto specifico
QUERY = """
query GetProduct($id: ID!) {
    product(id: $id) {
        id
        legacyResourceId
        title
        metafields(first: 20) {
            edges {
                node {
                    namespace
                    key
                    value
                }
            }
        }
        variants(first: 10) {
            edges {
                node {
                    id
                    legacyResourceId
                    title
                    sku
                    metafields(first: 30) {
                        edges {
                            node {
                                namespace
                                key
                                value
                            }
                        }
                    }
                }
            }
        }
    }
}
"""

def main():
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": ACCESS_TOKEN,
        "Content-Type": "application/json"
    }

    # ID prodotto in formato GID
    product_gid = "gid://shopify/Product/1507285532784"

    payload = {
        "query": QUERY,
        "variables": {"id": product_gid}
    }

    print(f"Chiamata a: {url}")
    print(f"Prodotto: {product_gid}")
    print("="*60)

    response = requests.post(url, json=payload, headers=headers)

    if response.status_code != 200:
        print(f"Errore HTTP: {response.status_code}")
        print(response.text)
        return

    data = response.json()

    if "errors" in data:
        print("Errori GraphQL:")
        print(json.dumps(data["errors"], indent=2))
        return

    product = data.get("data", {}).get("product", {})

    if not product:
        print("Prodotto non trovato!")
        return

    print(f"Prodotto: {product.get('title')} (ID: {product.get('legacyResourceId')})")

    # Metafield prodotto
    print("\n--- METAFIELD PRODOTTO ---")
    prod_mf = product.get("metafields", {}).get("edges", [])
    if prod_mf:
        for edge in prod_mf:
            mf = edge["node"]
            print(f"  {mf['namespace']}.{mf['key']}: {mf['value'][:80] if mf['value'] else 'None'}...")
    else:
        print("  Nessun metafield prodotto")

    # Varianti e metafield
    print("\n--- VARIANTI E METAFIELD ---")
    for var_edge in product.get("variants", {}).get("edges", []):
        var = var_edge["node"]
        print(f"\nVariante: {var.get('title')} (ID: {var.get('legacyResourceId')}, SKU: {var.get('sku')})")

        var_mf = var.get("metafields", {}).get("edges", [])
        if var_mf:
            for mf_edge in var_mf:
                mf = mf_edge["node"]
                val = mf['value'][:80] if mf['value'] else 'None'
                print(f"    {mf['namespace']}.{mf['key']}: {val}")
        else:
            print("    Nessun metafield variante")

if __name__ == "__main__":
    main()
