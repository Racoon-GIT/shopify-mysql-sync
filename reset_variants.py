import os
import json
import time
import requests
import mysql.connector

SHOPIFY_DOMAIN = os.getenv("SHOPIFY_DOMAIN")
SHOPIFY_TOKEN = os.getenv("SHOPIFY_TOKEN")
PRODUCT_IDS = os.getenv("PRODUCT_IDS")

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": SHOPIFY_TOKEN
}

def get_product_variants(product_id):
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{product_id}/variants.json"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()["variants"]

def update_variant(product_id, variant_id, payload):
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{product_id}/variants/{variant_id}.json"
    response = requests.put(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()

def delete_variant(product_id, variant_id):
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{product_id}/variants/{variant_id}.json"
    response = requests.delete(url, headers=HEADERS)
    response.raise_for_status()

def create_variant(product_id, payload):
    url = f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{product_id}/variants.json"
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    return response.json()

def ensure_backup_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            id BIGINT PRIMARY KEY,
            product_id BIGINT,
            variant_json JSON,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def backup_variants_to_db(product_id, variants):
    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cursor = conn.cursor()
    ensure_backup_table(cursor)

    for variant in variants:
        cursor.execute("""
            INSERT INTO backup_variants (id, product_id, variant_json)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                product_id = VALUES(product_id),
                variant_json = VALUES(variant_json)
        """, (variant["id"], product_id, json.dumps(variant)))

    conn.commit()
    conn.close()

def main():
    if not PRODUCT_IDS:
        print("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    for product_id in PRODUCT_IDS.split(","):
        product_id = product_id.strip()
        print(f"📦 Elaborazione prodotto: {product_id}")
        try:
            variants = get_product_variants(product_id)
            if not variants:
                print("⚠️ Nessuna variante trovata.")
                continue

            print(f"💾 Backup di {len(variants)} varianti…")
            backup_variants_to_db(product_id, variants)

            last_variant = variants[-1]
            dummy_payload = {
                "variant": {
                    "id": last_variant["id"],
                    "option1": "dummy",
                    "price": "0.01",
                    "sku": "dummy-sku",
                    "barcode": "",
                    "inventory_management": None,
                    "inventory_policy": "deny"
                }
            }
            update_variant(product_id, last_variant["id"], dummy_payload)

            for v in variants[:-1]:
                payload = {"variant": {
                    "option1": v["option1"],
                    "option2": v["option2"],
                    "option3": v["option3"],
                    "price": v["price"],
                    "compare_at_price": v.get("compare_at_price"),
                    "sku": v["sku"],
                    "barcode": v["barcode"],
                    "inventory_management": v.get("inventory_management"),
                    "inventory_policy": v["inventory_policy"],
                    "fulfillment_service": v["fulfillment_service"],
                    "requires_shipping": v["requires_shipping"]
                }}
                create_variant(product_id, payload)
                time.sleep(0.3)

            delete_variant(product_id, last_variant["id"])
            print("✅ Ricreazione completata.")
        except Exception as e:
            print(f"❌ Errore durante l'elaborazione del prodotto {product_id}: {e}")

if __name__ == "__main__":
    main()
