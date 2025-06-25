#!/usr/bin/env python3
# reset_variants.py

import os, sys, time, json
from decimal import Decimal
import requests, mysql.connector

# ---------- CONFIG -------------------------------------------------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST      = os.getenv("DB_HOST")
DB_USER      = os.getenv("DB_USER")
DB_PASS      = os.getenv("DB_PASS")
DB_NAME      = os.getenv("DB_NAME")
PRODUCT_IDS  = os.getenv("PRODUCT_IDS")  # Comma-separated product IDs

API_VERSION = "2024-04"
HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- LOG ----------------------------------------------------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------- DB SETUP ----------------------------------------------
DDL_BACKUP = """
CREATE TABLE IF NOT EXISTS backup_variants (
  Variant_id BIGINT PRIMARY KEY,
  product_id BIGINT,
  variant_json JSON
)
"""

# ---------- FUNCTIONS ---------------------------------------------
def get_product(pid):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{pid}.json"
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json()["product"]

def delete_variant(vid):
    try:
        url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{vid}.json"
        res = requests.delete(url, headers=HEADERS)
        if res.status_code == 200 or res.status_code == 204:
            return True
        log(f"⚠️  Impossibile eliminare variante {vid}: {res.status_code} {res.text}")
    except Exception as e:
        log(f"⚠️  Errore durante eliminazione variante {vid}: {e}")
    return False

def create_variant(pid, variant_data):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{pid}/variants.json"
    payload = json.dumps({"variant": variant_data})
    res = requests.post(url, headers=HEADERS, data=payload)
    res.raise_for_status()
    return res.json()

# ---------- MAIN ---------------------------------------------------
def main():
    if not PRODUCT_IDS:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cur = conn.cursor()
    cur.execute(DDL_BACKUP)
    conn.commit()

    for pid in PRODUCT_IDS.split(","):
        pid = pid.strip()
        if not pid:
            continue

        log(f"\n📦 Elaborazione prodotto: {pid}")
        try:
            prod = get_product(pid)
            variants = prod["variants"]
            log(f"💾 Backup di {len(variants)} varianti…")

            for v in variants:
                v_json = json.dumps(v, ensure_ascii=False)
                try:
                    cur.execute(
                        "REPLACE INTO backup_variants (Variant_id, product_id, variant_json) VALUES (%s, %s, %s)",
                        (v["id"], v["product_id"], v_json)
                    )
                except mysql.connector.Error as err:
                    log(f"❌ JSON error on variant {v['id']}: {err}\n🚨 JSON problematic content:\n{v_json}")

            conn.commit()

            for v in variants:
                delete_variant(v["id"])

            log("🔁 Ricreazione varianti…")
            cur.execute("SELECT variant_json FROM backup_variants WHERE product_id = %s ORDER BY Variant_id", (pid,))
            for (v_json,) in cur.fetchall():
                v_data = json.loads(v_json)
                fields_to_keep = {
                    "title", "price", "compare_at_price", "cost", "taxable", "sku", "barcode",
                    "inventory_management", "inventory_policy", "inventory_quantity",
                    "requires_shipping", "weight", "weight_unit", "fulfillment_service",
                    "option1", "option2", "option3", "harmonized_system_code", "country_code_of_origin",
                    "admin_graphql_api_id", "metafields"
                }
                clean_data = {k: v_data[k] for k in v_data if k in fields_to_keep and v_data[k] is not None}
                create_variant(pid, clean_data)

        except Exception as e:
            log(f"❌ Errore: {e}")

    cur.close()
    conn.close()

# ------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)
