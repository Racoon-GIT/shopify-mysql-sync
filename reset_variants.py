#!/usr/bin/env python3
# reset_variants.py

import os, sys, time, json
import requests, mysql.connector

# ---------- CONFIG --------------------------------------------------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST      = os.getenv("DB_HOST")
DB_USER      = os.getenv("DB_USER")
DB_PASS      = os.getenv("DB_PASS")
DB_NAME      = os.getenv("DB_NAME")
PRODUCT_IDS  = os.getenv("PRODUCT_IDS")
API_VERSION  = "2024-04"

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- UTILS --------------------------------------------------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def get_variants(product_id: str) -> list:
    res = requests.get(
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
        headers=HEADERS
    )
    res.raise_for_status()
    return res.json().get("variants", [])

def update_variant(product_id: str, variant_id: int, payload: dict):
    res = requests.put(
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{variant_id}.json",
        headers=HEADERS,
        json={"variant": {"id": variant_id, **payload}}
    )
    res.raise_for_status()
    return res.json()

def delete_variant(product_id: str, variant_id: int):
    res = requests.delete(
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants/{variant_id}.json",
        headers=HEADERS
    )
    res.raise_for_status()

def create_variant(product_id: str, payload: dict):
    res = requests.post(
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
        headers=HEADERS,
        json={"variant": payload}
    )
    res.raise_for_status()
    return res.json()

# ---------- DB SETUP ------------------------------------------------
def ensure_backup_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            product_id BIGINT,
            variant_json JSON,
            PRIMARY KEY (product_id, JSON_UNQUOTE(JSON_EXTRACT(variant_json, '$.id')))
        )
    """)

def backup_variants(cursor, conn, product_id, variants):
    log(f"💾 Backup di {len(variants)} varianti…")
    cursor.execute("DELETE FROM backup_variants WHERE product_id = %s", (product_id,))
    for v in variants:
        try:
            payload = json.dumps(v, ensure_ascii=False)
            cursor.execute(
                "INSERT INTO backup_variants (product_id, variant_json) VALUES (%s, %s)",
                (product_id, payload)
            )
        except Exception as e:
            log(f"🚨 JSON error on variant {v.get('id')}: {e}")
            log("🚨 JSON problematic content:")
            log(json.dumps(v, indent=2))
    conn.commit()

def load_backup_variants(cursor, product_id) -> list:
    cursor.execute(
        "SELECT variant_json FROM backup_variants WHERE product_id = %s ORDER BY variant_json->>'$.position'",
        (product_id,)
    )
    return [json.loads(row[0]) for row in cursor.fetchall()]

# ---------- MAIN ----------------------------------------------------
def main():
    if not PRODUCT_IDS:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cur = conn.cursor()
    ensure_backup_table(cur)

    for pid in PRODUCT_IDS.split(","):
        pid = pid.strip()
        if not pid:
            continue

        log(f"📦 Elaborazione prodotto: {pid}")
        try:
            variants = get_variants(pid)
            backup_variants(cur, conn, pid, variants)

            if not variants:
                log("⚠️ Nessuna variante trovata.")
                continue

            survivor = variants[0]
            survivor_id = survivor["id"]

            # Rinomina variante superstite
            log("✏️  Rinomina variante superstite…")
            dummy_data = {
                "option1": "TO_DELETE",
                "price": "9999.99",
                "sku": "DUMMY-SKU-TO-DELETE",
                "inventory_quantity": 0,
                "inventory_management": "shopify"
            }
            update_variant(pid, survivor_id, dummy_data)

            # Ricrea varianti
            backup = load_backup_variants(cur, pid)
            log("🔁 Ricreazione varianti…")
            for v in backup:
                v.pop("id", None)
                v.pop("product_id", None)
                create_variant(pid, v)

            # Cancella dummy
            log("🗑️  Eliminazione variante dummy…")
            delete_variant(pid, survivor_id)

        except Exception as e:
            log(f"❌ Errore: {e}")

    cur.close()
    conn.close()

# ---------- ENTRY POINT --------------------------------------------
if __name__ == "__main__":
    main()
