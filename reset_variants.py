import os
import json
import requests
import mysql.connector
from datetime import datetime

SHOPIFY_DOMAIN = os.environ.get("SHOPIFY_DOMAIN")
SHOPIFY_API_KEY = os.environ.get("SHOPIFY_API_KEY")
SHOPIFY_PASSWORD = os.environ.get("SHOPIFY_PASSWORD")
PRODUCT_IDS = os.environ.get("PRODUCT_IDS")

DB_HOST = os.environ.get("DB_HOST")
DB_USER = os.environ.get("DB_USER")
DB_PASSWORD = os.environ.get("DB_PASSWORD")
DB_NAME = os.environ.get("DB_NAME")

def shopify_headers():
    return {
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": SHOPIFY_PASSWORD
    }

def connect_db():
    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),  
        database=os.getenv("DB_NAME")
    )

def ensure_backup_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            product_id BIGINT,
            variant_id BIGINT,
            variant_json JSON,
            backup_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def backup_variants(cursor, product_id, variants):
    for variant in variants:
        cursor.execute(
            """
            INSERT INTO backup_variants (product_id, variant_id, variant_json)
            VALUES (%s, %s, %s)
            """,
            (product_id, variant['id'], json.dumps(variant))
        )

def delete_all_variants(product_id, variants):
    if len(variants) <= 1:
        return
    
    # Rename last variant as dummy
    last_variant = variants[-1]
    variant_id = last_variant['id']
    dummy_payload = {
        "variant": {
            "id": variant_id,
            "option1": "TO_DELETE",
            "sku": f"DUMMY-{datetime.now().timestamp()}"
        }
    }
    requests.put(
        f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/variants/{variant_id}.json",
        headers=shopify_headers(),
        json=dummy_payload
    )

    # Delete all other variants
    for v in variants[:-1]:
        requests.delete(
            f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/variants/{v['id']}.json",
            headers=shopify_headers()
        )

def create_variant_safe(product_id, variant_data):
    try:
        res = requests.post(
            f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{product_id}/variants.json",
            headers=shopify_headers(),
            json={"variant": variant_data}
        )
        res.raise_for_status()
        return res.json()
    except requests.exceptions.HTTPError as e:
        print(f"❌ Shopify API error: {res.status_code}")
        try:
            print("🧾", json.dumps(res.json(), indent=2))
        except:
            print(res.text)
        raise

def recreate_variants(product_id, variants):
    dummy_id = variants[-1]['id'] if len(variants) > 0 else None
    for v in variants:
        if v['id'] == dummy_id:
            continue  # skip dummy
        v.pop('id', None)
        v.pop('admin_graphql_api_id', None)
        create_variant_safe(product_id, v)
    if dummy_id:
        requests.delete(
            f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/variants/{dummy_id}.json",
            headers=shopify_headers()
        )

def main():
    if not PRODUCT_IDS:
        print("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    ids = [pid.strip() for pid in PRODUCT_IDS.split(",") if pid.strip()]

    db = connect_db()
    cur = db.cursor()
    ensure_backup_table(cur)

    for pid in ids:
        try:
            print(f"📦 Elaborazione prodotto: {pid}")
            res = requests.get(
                f"https://{SHOPIFY_DOMAIN}/admin/api/2024-04/products/{pid}/variants.json",
                headers=shopify_headers()
            )
            variants = res.json().get("variants", [])

            print(f"💾 Backup di {len(variants)} varianti…")
            backup_variants(cur, pid, variants)
            db.commit()

            delete_all_variants(pid, variants)
            recreate_variants(pid, variants)

            print("✅ Ricreazione completata.")
        except Exception as e:
            print(f"❌ Errore durante l'elaborazione del prodotto {pid}: {e}")

if __name__ == "__main__":
    main()
