import os
import requests
import json
import mysql.connector
from datetime import datetime
from time import sleep

SHOP_URL = os.getenv("SHOP_URL")
API_KEY = os.getenv("SHOPIFY_API_KEY")
PASSWORD = os.getenv("SHOPIFY_API_PASSWORD")
PRODUCT_IDS = os.getenv("PRODUCT_IDS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "3306")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

HEADERS = {
    "Content-Type": "application/json",
    "X-Shopify-Access-Token": PASSWORD
}

def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def get_product_variants(product_id):
    url = f"https://{SHOP_URL}/admin/api/2024-04/products/{product_id}/variants.json"
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json()["variants"]

def delete_variant(variant_id):
    url = f"https://{SHOP_URL}/admin/api/2024-04/variants/{variant_id}.json"
    res = requests.delete(url, headers=HEADERS)
    if res.status_code != 200 and res.status_code != 204:
        log(f"⚠️ Errore eliminazione variante {variant_id}: {res.status_code} {res.text}")

def create_variant(product_id, variant):
    url = f"https://{SHOP_URL}/admin/api/2024-04/products/{product_id}/variants.json"
    res = requests.post(url, headers=HEADERS, json={"variant": variant})
    res.raise_for_status()
    return res.json()["variant"]

def ensure_backup_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            product_id BIGINT NOT NULL,
            variant_id BIGINT NOT NULL,
            variant_json JSON NOT NULL,
            PRIMARY KEY (product_id, variant_id)
        )
    """)

def backup_variants(cursor, conn, product_id, variants):
    log(f"💾 Backup di {len(variants)} varianti…")
    cursor.execute("DELETE FROM backup_variants WHERE product_id = %s", (product_id,))
    for v in variants:
        try:
            variant_id = v.get("id")
            payload = json.dumps(v, ensure_ascii=False)
            cursor.execute(
                "INSERT INTO backup_variants (product_id, variant_id, variant_json) VALUES (%s, %s, %s)",
                (product_id, variant_id, payload)
            )
        except Exception as e:
            log(f"🚨 JSON error on variant {v.get('id')}: {e}")
            log("🚨 JSON problematic content:")
            log(json.dumps(v, indent=2))
    conn.commit()

def load_backup_variants(cursor, product_id) -> list:
    cursor.execute(
        "SELECT variant_json FROM backup_variants WHERE product_id = %s ORDER BY variant_id",
        (product_id,)
    )
    return [json.loads(row[0]) for row in cursor.fetchall()]

def reset_variants(product_id):
    log(f"📦 Elaborazione prodotto: {product_id}")
    variants = get_product_variants(product_id)

    if not variants:
        log("⚠️ Nessuna variante trovata")
        return

    dummy_variant = variants[-1]
    dummy_variant_id = dummy_variant["id"]

    dummy_variant["option1"] = "DUMMY"
    dummy_variant["sku"] = f"DUMMY-{dummy_variant_id}"
    dummy_variant["price"] = "0.00"
    dummy_variant["compare_at_price"] = None
    dummy_variant["barcode"] = ""
    dummy_variant["inventory_quantity"] = 0
    dummy_variant["inventory_management"] = "shopify"
    dummy_variant["inventory_policy"] = "deny"

    url = f"https://{SHOP_URL}/admin/api/2024-04/variants/{dummy_variant_id}.json"
    res = requests.put(url, headers=HEADERS, json={"variant": dummy_variant})
    res.raise_for_status()

    sleep(1)

    variants_to_backup = [v for v in variants if v["id"] != dummy_variant_id]

    conn = mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    cur = conn.cursor()
    ensure_backup_table(cur)
    backup_variants(cur, conn, product_id, variants_to_backup)
    cur.close()
    conn.close()

    for v in variants_to_backup:
        delete_variant(v["id"])
        sleep(0.5)

    sleep(1)
    log("🔁 Ricreazione varianti…")

    conn = mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME
    )
    cur = conn.cursor()
    variants_data = load_backup_variants(cur, product_id)
    cur.close()
    conn.close()

    for v in variants_data:
        v.pop("id", None)
        v.pop("product_id", None)
        v.pop("position", None)
        try:
            create_variant(product_id, v)
            sleep(0.5)
        except Exception as e:
            log(f"❌ Errore: {e}")
            log("🚨 JSON problematic content:")
            log(json.dumps(v, indent=2))

    delete_variant(dummy_variant_id)
    log("✅ Completato")

def main():
    if not PRODUCT_IDS:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    for pid in PRODUCT_IDS.split(","):
        pid = pid.strip()
        if pid:
            try:
                reset_variants(pid)
            except Exception as e:
                log(f"❌ Errore durante l'elaborazione del prodotto {pid}: {e}")

if __name__ == "__main__":
    main()