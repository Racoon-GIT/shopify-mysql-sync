#!/usr/bin/env python3
# reset_variants.py

import os
import sys
import time
import json
import requests
import mysql.connector
from decimal import Decimal

# --- CONFIG ---
SHOPIFY_SHOP = os.getenv("SHOPIFY_SHOP")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")
PRODUCT_IDS = os.getenv("PRODUCT_IDS")  # comma-separated
API_VERSION = "2024-04"

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# --- UTILS ---
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

def get_variants(product_id):
    url = f"https://{SHOPIFY_SHOP}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json().get("variants", [])

def delete_variant(product_id, variant_id):
    url = f"https://{SHOPIFY_SHOP}/admin/api/{API_VERSION}/products/{product_id}/variants/{variant_id}.json"
    res = requests.delete(url, headers=HEADERS)
    if res.status_code != 200:
        log(f"⚠️  Errore eliminazione variante {variant_id}: {res.status_code} - {res.text}")

def create_variant(product_id, data):
    url = f"https://{SHOPIFY_SHOP}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    payload = json.dumps({"variant": data})
    res = requests.post(url, headers=HEADERS, data=payload)
    res.raise_for_status()
    return res.json().get("variant")

def main():
    if not PRODUCT_IDS:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        sys.exit(1)

    product_ids = PRODUCT_IDS.split(",")
    db = connect_db()
    cur = db.cursor()

    for pid in product_ids:
        log(f"📦 Elaborazione prodotto: {pid}")

        try:
            cur.execute("SELECT variant_json FROM variant_backup WHERE product_id = %s ORDER BY id", (pid,))
            rows = cur.fetchall()
            backup_variants = [json.loads(row[0]) for row in rows]

            existing = get_variants(pid)
            if not existing:
                log("❌ Nessuna variante trovata, skip.")
                continue

            dummy = existing[0]
            dummy_id = dummy["id"]
            dummy_data = {
                "option1": "dummy",
                "price": "9999",
                "sku": f"DUMMY-{int(time.time())}"
            }

            # Aggiorna variante dummy
            url = f"https://{SHOPIFY_SHOP}/admin/api/{API_VERSION}/variants/{dummy_id}.json"
            requests.put(url, headers=HEADERS, json={"variant": dummy_data}).raise_for_status()

            # Crea nuove varianti
            for v in backup_variants:
                v.pop("id", None)
                v.pop("admin_graphql_api_id", None)
                create_variant(pid, v)

            delete_variant(pid, dummy_id)
            log("✅ Ricreazione completata.")

        except Exception as e:
            log(f"❌ Errore durante l'elaborazione del prodotto {pid}: {e}")

    cur.close()
    db.close()

if __name__ == "__main__":
    main()
