#!/usr/bin/env python3
# reset_variants.py

import os
import sys
import time
import json
import requests
import mysql.connector
from mysql.connector import Error

# ---------- CONFIG ----------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
API_VERSION  = "2024-04"

DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- LOG ----------
def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

# ---------- DB CONNECTION ----------
def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

# ---------- RESET VARIANTS ----------
def backup_and_reset_variants(product_id):
    try:
        # Recupera varianti
        res = requests.get(
            f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
            headers=HEADERS
        )
        res.raise_for_status()
        variants = res.json().get("variants", [])

        if not variants:
            log(f"⚠️  Nessuna variante trovata per il prodotto {product_id}")
            return

        # Connessione DB
        conn = connect_db()
        cur = conn.cursor()

        # Crea tabella di appoggio se non esiste
        cur.execute("""
        CREATE TABLE IF NOT EXISTS tmp_variant_backup (
            variant_json JSON
        )""")
        cur.execute("TRUNCATE TABLE tmp_variant_backup")

        # Salva varianti
        for v in variants:
            cur.execute("INSERT INTO tmp_variant_backup (variant_json) VALUES (%s)",
                        (json.dumps(v),))
        conn.commit()
        log(f"💾 Backup di {len(variants)} varianti…")

        # Elimina tutte le varianti tranne una
        dummy_id = variants[0]['id']
        for v in variants[1:]:
            requests.delete(
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{v['id']}.json",
                headers=HEADERS
            )
        log("🗑️  Varianti eliminate (eccetto dummy)…")

        # Rinomina la variante superstite con dati dummy
        requests.put(
            f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
            headers=HEADERS,
            json={"variant": {
                "id": dummy_id,
                "option1": "__TO_DELETE__",
                "price": "9999.99",
                "sku": "DUMMY",
                "barcode": "DUMMY"
            }}
        )

        # Ricrea le varianti salvate (escludi quella dummy)
        cur.execute("SELECT variant_json FROM tmp_variant_backup")
        for (variant_json,) in cur.fetchall():
            v = json.loads(variant_json)
            if v['id'] == dummy_id:
                continue

            v.pop("id", None)
            v.pop("product_id", None)
            res = requests.post(
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
                headers=HEADERS,
                json={"variant": v}
            )
            if res.status_code >= 400:
                log(f"❌ Errore JSON: {res.text}")

        # Elimina la variante dummy
        requests.delete(
            f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
            headers=HEADERS
        )

        log("✅ Ricreazione completata.")

    except requests.exceptions.RequestException as e:
        log(f"❌ Errore durante l'accesso alle varianti: {e}")
    except Error as e:
        log(f"❌ Errore MySQL: {e}")
    finally:
        try:
            cur.close()
            conn.close()
        except:
            pass

# ---------- MAIN ----------
def main():
    product_ids_env = os.getenv("PRODUCT_IDS")
    if not product_ids_env:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        sys.exit(1)

    product_ids = [pid.strip() for pid in product_ids_env.split(",") if pid.strip()]

    for product_id in product_ids:
        log(f"📦 Elaborazione prodotto: {product_id}")
        backup_and_reset_variants(product_id)

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)
