import os
import json
import requests
import mysql.connector
from mysql.connector import connect, Error

# ENV vars
SHOPIFY_API_KEY = os.getenv("SHOPIFY_API_KEY")
SHOPIFY_API_PASSWORD = os.getenv("SHOPIFY_API_PASSWORD")
SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")

DB_HOST = os.getenv("DB_HOST")
DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

# Prodotto da processare (singolo ID per questa versione)
PRODUCT_IDS = [14950675480908]


def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )


def ensure_backup_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            product_id BIGINT,
            variant_id BIGINT,
            variant_json JSON,
            PRIMARY KEY (product_id, variant_id)
        );
    """)


def get_product_variants(product_id):
    url = f"https://{SHOPIFY_API_KEY}:{SHOPIFY_API_PASSWORD}@{SHOPIFY_SHOP}/admin/api/2024-04/products/{product_id}/variants.json"
    response = requests.get(url)
    response.raise_for_status()
    return response.json().get("variants", [])


def delete_all_variants(product_id, variants):
    for variant in variants:
        variant_id = variant["id"]
        url = f"https://{SHOPIFY_API_KEY}:{SHOPIFY_API_PASSWORD}@{SHOPIFY_SHOP}/admin/api/2024-04/products/{product_id}/variants/{variant_id}.json"
        response = requests.delete(url)
        if response.status_code != 200:
            print(f"⚠️ Impossibile eliminare variante {variant_id}: {response.status_code}")


def create_variant(product_id, variant_data):
    url = f"https://{SHOPIFY_API_KEY}:{SHOPIFY_API_PASSWORD}@{SHOPIFY_SHOP}/admin/api/2024-04/products/{product_id}/variants.json"
    response = requests.post(url, json={"variant": variant_data})
    if response.status_code != 201:
        print(f"⚠️ Errore creazione variante: {response.status_code} - {response.text}")
    return response.json()


def process_product(product_id, cur, conn):
    print(f"📦 Elaborazione prodotto: {product_id}")

    # 1. Recupera varianti originali
    try:
        variants = get_product_variants(product_id)
    except Exception as e:
        print(f"❌ Errore durante l'accesso alle varianti: {e}")
        return

    print(f"💾 Backup di {len(variants)} varianti…")
    for variant in variants:
        cur.execute(
            "INSERT INTO backup_variants (product_id, variant_id, variant_json) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE variant_json = VALUES(variant_json)",
            (product_id, variant["id"], json.dumps(variant))
        )
    conn.commit()

    if len(variants) == 0:
        print("⚠️ Nessuna variante da gestire.")
        return

    # 2. Crea variante dummy su ultima variante superstite
    dummy_variant = variants[-1].copy()
    dummy_variant["option1"] = "DUMMY"
    dummy_variant["sku"] = "DUMMY-SKU"
    dummy_variant["barcode"] = "DUMMY-BARCODE"

    create_variant(product_id, dummy_variant)

    # 3. Elimina tutte le vecchie varianti
    delete_all_variants(product_id, variants)

    # 4. Recupera le varianti salvate e ricreale
    cur.execute("SELECT variant_json FROM backup_variants WHERE product_id = %s", (product_id,))
    rows = cur.fetchall()

    for (variant_json,) in rows:
        variant_data = json.loads(variant_json)

        # Rimuove campi che non possono essere inviati nella creazione
        for key in ["id", "product_id", "admin_graphql_api_id", "created_at", "updated_at", "inventory_item_id"]:
            variant_data.pop(key, None)

        create_variant(product_id, variant_data)

    # 5. Elimina la variante dummy
    new_variants = get_product_variants(product_id)
    for v in new_variants:
        if v["sku"] == "DUMMY-SKU":
            url = f"https://{SHOPIFY_API_KEY}:{SHOPIFY_API_PASSWORD}@{SHOPIFY_SHOP}/admin/api/2024-04/products/{product_id}/variants/{v['id']}.json"
            requests.delete(url)
            break

    print("✅ Ricreazione completata.")


def main():
    try:
        db = connect_db()
        cur = db.cursor()
        ensure_backup_table(cur)

        for product_id in PRODUCT_IDS:
            try:
                process_product(product_id, cur, db)
            except Exception as e:
                print(f"❌ Errore durante l'elaborazione del prodotto {product_id}: {e}")

        cur.close()
        db.close()
    except Error as err:
        print(f"❌ Connessione MySQL fallita: {err}")


if __name__ == "__main__":
    main()
