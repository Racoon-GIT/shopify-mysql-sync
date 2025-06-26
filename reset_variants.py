import os
import sys
import time
import json
import requests
import mysql.connector
from mysql.connector import Error

# ---------- CONFIG -------------------------------------------------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST      = os.getenv("DB_HOST")
DB_USER      = os.getenv("DB_USER")
DB_PASS      = os.getenv("DB_PASS")
DB_NAME      = os.getenv("DB_NAME")
API_VERSION  = "2024-04"
HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- LOG ----------------------------------------------------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------- UTILS --------------------------------------------------
def safe_request(method, url, headers=None, json=None, max_retries=5):
    for attempt in range(max_retries):
        res = requests.request(method, url, headers=headers, json=json)
        if res.status_code == 429:
            wait = 2 ** attempt
            log(f"⚠️ Rate limit, attendo {wait}s...")
            time.sleep(wait)
            continue
        res.raise_for_status()
        return res
    raise Exception("❌ Troppe richieste, impossibile continuare")

# ---------- DB ------------------------------------------------------
def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

def ensure_temp_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS variant_backup (
            product_id BIGINT,
            variant_json JSON
        )
    """)
    cur.execute("DELETE FROM variant_backup")

# ---------- MAIN ----------------------------------------------------
def main():
    product_ids_env = os.getenv("PRODUCT_IDS")
    if not product_ids_env:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        sys.exit(1)

    product_ids = [pid.strip() for pid in product_ids_env.split(",") if pid.strip()]
    db = connect_db()
    cur = db.cursor()
    ensure_temp_table(cur)

    for product_id in product_ids:
        log(f"📦 Elaborazione prodotto: {product_id}")

        try:
            res = safe_request(
                "GET",
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
                headers=HEADERS
            )
            variants = res.json().get("variants", [])
        except Exception as e:
            log(f"❌ Errore durante l'accesso alle varianti: {e}")
            continue

        log(f"💾 Backup di {len(variants)} varianti…")
        for v in variants:
            cur.execute(
                "INSERT INTO variant_backup (product_id, variant_json) VALUES (%s, %s)",
                (product_id, json.dumps(v))
            )
        db.commit()

        # Rinomina variante superstite
        if variants:
            dummy_id = variants[0]["id"]
            try:
                safe_request(
                    "PUT",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
                    headers=HEADERS,
                    json={"variant": {"id": dummy_id, "option1": "DUMMY", "sku": "DUMMY", "barcode": "DUMMY"}}
                )
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore dummy update: {e}")

        # Cancella tutte le varianti tranne dummy
        for v in variants[1:]:
            try:
                safe_request(
                    "DELETE",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{v['id']}.json",
                    headers=HEADERS
                )
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore delete variante {v['id']}: {e}")

        log("🗑️  Varianti eliminate (eccetto dummy)…")

        # Ricrea varianti da backup
        cur.execute("SELECT variant_json FROM variant_backup WHERE product_id = %s", (product_id,))
        rows = cur.fetchall()
        for (variant_json,) in rows:
            v = json.loads(variant_json)
            if v["option1"] == "DUMMY":
                continue
            payload = {"variant": {
                "option1": v["option1"],
                "option2": v.get("option2"),
                "option3": v.get("option3"),
                "price": v.get("price"),
                "compare_at_price": v.get("compare_at_price"),
                "sku": v.get("sku"),
                "barcode": v.get("barcode"),
                "inventory_management": v.get("inventory_management"),
                "inventory_policy": v.get("inventory_policy"),
                "fulfillment_service": v.get("fulfillment_service"),
                "requires_shipping": v.get("requires_shipping", True),
                "taxable": v.get("taxable", True),
                "weight": v.get("weight", 0),
                "weight_unit": v.get("weight_unit", "kg")
            }}
            try:
                safe_request(
                    "POST",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
                    headers=HEADERS,
                    json=payload
                )
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore JSON: {e}")

        # Elimina la dummy variant finale
        try:
            safe_request(
                "DELETE",
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
                headers=HEADERS
            )
            log("✅ Ricreazione completata.")
            time.sleep(0.6)
        except Exception as e:
            log(f"❌ Errore eliminazione dummy: {e}")

    cur.close()
    db.close()

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Errore fatale: {e}")
        sys.exit(1)
