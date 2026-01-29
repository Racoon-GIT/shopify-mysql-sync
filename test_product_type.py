#!/usr/bin/env python3
"""Test: verifica campo productType."""

import requests

SHOP_DOMAIN = "racoon-lab.myshopify.com"
ACCESS_TOKEN = "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"

QUERY = """
query GetProduct($id: ID!) {
    product(id: $id) {
        title
        productType
        metafields(first: 5, namespace: "mm-google-shopping") {
            edges {
                node {
                    key
                    value
                }
            }
        }
    }
}
"""

response = requests.post(
    f"https://{SHOP_DOMAIN}/admin/api/2024-04/graphql.json",
    json={"query": QUERY, "variables": {"id": "gid://shopify/Product/1507285532784"}},
    headers={"X-Shopify-Access-Token": ACCESS_TOKEN, "Content-Type": "application/json"}
)

data = response.json()
product = data.get("data", {}).get("product", {})

print(f"Titolo: {product.get('title')}")
print(f"Product Type (campo nativo Shopify): {product.get('productType')}")
print()
print("Metafield mm-google-shopping:")
for edge in product.get("metafields", {}).get("edges", []):
    mf = edge["node"]
    if mf["key"] == "google_product_category":
        print(f"  google_product_category: {mf['value']}")
