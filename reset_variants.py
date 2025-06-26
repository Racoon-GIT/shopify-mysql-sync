#!/usr/bin/env python3
# reset_variants.py
# --------------------------------------------------
# Per ciascun prodotto indicato, salva tutte le varianti
# in una tabella temporanea, cancella tutte le varianti
# reali su Shopify, le ricrea da zero, e infine elimina
# la variante dummy iniziale.
# --------------------------------------------------

import os
import sys
import time
import json
import requests
import mysql.connector

# ---------- CONFIG -------------------------------------------------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST      = os.getenv("DB_HOST")
DB_USER      = os.getenv("DB_USER")
DB_PASS      = os.getenv("DB_PASS")
DB_NAME      = os.getenv("DB_NAME")

API_VERSION = "2024-04"
HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- LOG ----------------------------------------------------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# ---------- DB CONNECTION ------------------------------------------
def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

# ---------- BACKUP VARIANTI ----------------------------------------
def backup_variants(cur, conn, product_id):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    try:
        res = requests.get(url, headers=HEADERS); res.raise_for_status()
    except Exception as e:
        log(f"❌ Errore durante l'accesso alle varianti: {e}")
        return []

    variants = res.json().get("variants", [])
    log(f"💾 Backup di {len(variants)} varianti…")

    for v in variants:
        cur.execute(
            "REPLACE INTO variant_backup (variant_id, variant_json) VALUES (%s, %s)",
            (v["id"], json.dumps(v))
        )
    conn.commit()
    return variants

# ---------- CANCELLA VARIANTI --------------------------------------
def delete_all_variants(product_id, variants):
    for v in variants:
        variant_id = v["id"]
        url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{variant_id}.json"
        try:
            requests.delete(url, headers=HEADERS)
        except Exception as e:
            log(f"⚠️  Errore nel cancellare variante {variant_id}: {e}")
        time.sleep(0.5)  # throttle

# ---------- CREA VARIANTE DUMMY ------------------------------------
def create_dummy_variant(product_id):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    dummy = {
        "variant": {
            "option1": "DUMMY",
            "price": "9999.99",
            "sku": "DUMMY-VARIANT"
        }
    }
    res = requests.post(url, headers=HEADERS, json=dummy); res.raise_for_status()
    return res.json()["variant"]["id"]

# ---------- RECREA VARIANTI ----------------------------------------
def recreate_variants(cur, conn, product_id):
    cur.execute("SELECT variant_json FROM variant_backup")
    all_rows = cur.fetchall()
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    for (row,) in all_rows:
        v = json.loads(row)
        payload = {"variant": {
            "option1": v["option1"],
            "option2": v["option2"],
            "option3": v["option3"],
            "price": v["price"],
            "compare_at_price": v["compare_at_price"],
            "sku": v["sku"],
            "barcode": v["barcode"],
            "inventory_management": v["inventory_management"],
            "inventory_policy": v["inventory_policy"],
            "fulfillment_service": v["fulfillment_service"],
            "requires_shipping": v["requires_shipping"],
            "taxable": v["taxable"]
        }}
        try:
            requests.post(url, headers=HEADERS, json=payload)
        except Exception as e:
            log(f"⚠️  Errore nel creare variante {v.get('sku')}: {e}")
        time.sleep(0.5)

# ---------- ELIMINA VARIANTE DUMMY --------------------------------
def delete_variant(variant_id):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{variant_id}.json"
    requests.delete(url, headers=HEADERS)

# ---------- MAIN ---------------------------------------------------
def main():
    conn = connect_db()
    cur = conn.cursor()

    # Tabella temporanea per backup varianti
    cur.execute("""
    CREATE TABLE IF NOT EXISTS variant_backup (
      variant_id   BIGINT PRIMARY KEY,
      variant_json JSON NOT NULL
    )""")
    cur.execute("TRUNCATE TABLE variant_backup")
    conn.commit()

    product_ids = sys.argv[1:]
    if not product_ids:
        log("⚠️  Nessun ID prodotto specificato.")
        return

    for pid in product_ids:
        log(f"\U0001F4E6 Elaborazione prodotto: {pid}")
        variants = backup_variants(cur, conn, pid)
        if not variants:
            continue
        dummy_id = create_dummy_variant(pid)
        delete_all_variants(pid, variants)
        recreate_variants(cur, conn, pid)
        delete_variant(dummy_id)
        log("✅ Ricreazione completata.")

    cur.close()
    conn.close()

# ---------- ENTRY POINT --------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)
