import os
import json
import time
import requests
import mysql.connector
from mysql.connector import Error

SHOP_URL = os.getenv("SHOP_URL")
ACCESS_TOKEN = os.getenv("SHOPIFY_ACCESS_TOKEN")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": ACCESS_TOKEN,
}

def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )

def ensure_backup_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            product_id BIGINT,
            variant_json JSON,
            PRIMARY KEY(product_id, JSON_UNQUOTE(JSON_EXTRACT(variant_json, '$.id')))
        )
    """)

def get_variants(product_id):
    url = f"https://{SHOP_URL}/admin/api/2024-04/products/{product_id}.json"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    product_data = response.json()
    variants = product_data.get("product", {}).get("variants", [])
    return variants

def delete_variant(product_id, variant_id):
    url = f"https://{SHOP_URL}/admin/api/2024-04/products/{product_id}/variants/{variant_id}.json"
    response = requests.delete(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"⚠️ Errore nella cancellazione della variante {variant_id}: {response.status_code} - {response.text}")
    time.sleep(0.5)

def create_variant(product_id, variant_data):
    url = f"https://{SHOP_URL}/admin/api/2024-04/products/{product_id}/variants.json"
    response = requests.post(url, headers=HEADERS, json={"variant": variant_data})
    if response.status_code != 201:
        print(f"⚠️ Errore nella creazione variante: {response.status_code} - {response.text}")
    time.sleep(0.5)

def backup_variants(cursor, product_id, variants):
    for variant in variants:
        cursor.execute(
            "REPLACE INTO backup_variants (product_id, variant_json) VALUES (%s, %s)",
            (product_id, json.dumps(variant))
        )

def recreate_variants(product_id):
    print(f"\U0001F4E6 Elaborazione prodotto: {product_id}")
    db = connect_db()
    cursor = db.cursor()
    ensure_backup_table(cursor)

    try:
        variants = get_variants(product_id)
        print(f"\U0001F4BE Backup di {len(variants)} varianti…")
        if not variants:
            print("⚠️ Nessuna variante trovata, procedura interrotta.")
            return

        # Backup
        backup_variants(cursor, product_id, variants)
        db.commit()

        # Dummy update su ultima variante superstite
        last_variant_id = variants[-1]["id"]
        update_url = f"https://{SHOP_URL}/admin/api/2024-04/variants/{last_variant_id}.json"
        requests.put(update_url, headers=HEADERS, json={"variant": {"option1": "DUMMY", "sku": "DUMMY"}})
        time.sleep(1)

        # Ricreo tutte le altre
        for variant in variants[:-1]:
            variant_data = variant.copy()
            for key in ["id", "product_id", "admin_graphql_api_id", "created_at", "updated_at", "inventory_item_id"]:
                variant_data.pop(key, None)
            create_variant(product_id, variant_data)

        # Elimino la variante dummy
        delete_variant(product_id, last_variant_id)
        print("✅ Ricreazione completata.")

    except requests.exceptions.RequestException as e:
        print(f"❌ Errore Shopify: {e}")
    except Error as db_err:
        print(f"❌ Errore MySQL: {db_err}")
    finally:
        cursor.close()
        db.close()

def main():
    product_ids = os.getenv("PRODUCT_IDS", "").split(",")
    for pid in product_ids:
        if pid.strip():
            try:
                recreate_variants(int(pid.strip()))
            except Exception as e:
                print(f"❌ Errore durante l'elaborazione del prodotto {pid.strip()}: {e}")

if __name__ == "__main__":
    main()
