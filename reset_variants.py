#!/usr/bin/env python3
# reset_variants.py

import os, sys, time, json
import requests, mysql.connector
from decimal import Decimal

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

# ---------- MAIN ---------------------------------------------------
def main():
    ids_raw = os.getenv("PRODUCT_IDS")  # es: "1234567890,9876543210"
    if not ids_raw:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        return

    product_ids = [pid.strip() for pid in ids_raw.split(",") if pid.strip().isdigit()]
    if not product_ids:
        log("❌ Nessun ID prodotto valido trovato in input")
        return

    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS backup_variants (
            Product_id   BIGINT,
            Variant_id   BIGINT,
            variant_json JSON,
            PRIMARY KEY (Variant_id)
        )
    """)
    conn.commit()

    for pid in product_ids:
        try:
            log(f"\n📦 Elaborazione prodotto: {pid}")
            url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{pid}.json"
            res = requests.get(url, headers=HEADERS)
            res.raise_for_status()
            product = res.json().get("product")

            if not product or "variants" not in product:
                log("⚠️  Nessuna variante trovata")
                continue

            variants = product["variants"]
            log(f"💾 Backup di {len(variants)} varianti…")
            for v in variants:
                try:
                    variant_data = json.dumps(v, ensure_ascii=False)
                    cur.execute(
                        "INSERT INTO backup_variants (Product_id, Variant_id, variant_json) VALUES (%s, %s, %s) \
                         ON DUPLICATE KEY UPDATE variant_json = VALUES(variant_json)",
                        (pid, v["id"], variant_data)
                    )
                except Exception as e:
                    log(f"❌ JSON error on variant {v['id']}: {e}")
                    log("🚨 JSON problematic content:")
                    log(json.dumps(v))
                    raise

            conn.commit()

            # Cancellazione varianti (una a una)
            for v in variants[:-1]:  # lascia almeno 1 variante
                vid = v["id"]
                try:
                    del_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{vid}.json"
                    res = requests.delete(del_url, headers=HEADERS)
                    res.raise_for_status()
                    log(f"🗑️  Eliminata variante {vid}")
                except Exception as e:
                    log(f"❌ Errore eliminando variante {vid}: {e}")

            # Elimina l'ultima variante modificandola prima
            last = variants[-1]
            try:
                vid = last["id"]
                update_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{vid}.json"
                data = {"variant": {"id": vid, "option1": "Temp", "price": "0.01"}}
                res = requests.put(update_url, headers=HEADERS, json=data)
                res.raise_for_status()
                res = requests.delete(update_url, headers=HEADERS)
                res.raise_for_status()
                log(f"🗑️  Eliminata ultima variante {vid} con workaround")
            except Exception as e:
                log(f"❌ Errore eliminando ultima variante {vid}: {e}")

            # Ricreazione varianti
            log("🛠️  Ricreazione varianti…")
            cur.execute(
                "SELECT variant_json FROM backup_variants WHERE Product_id = %s",
                (pid,)
            )
            for (variant_json,) in cur.fetchall():
                variant_data = json.loads(variant_json)
                variant_data.pop("id", None)
                variant_data.pop("product_id", None)
                res = requests.post(
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants.json",
                    headers=HEADERS,
                    json={"variant": variant_data}
                )
                if res.status_code >= 400:
                    log(f"❌ Errore creando variante: {res.text}")
                else:
                    log(f"✅ Variante creata: {variant_data.get('title')}")

        except Exception as e:
            log(f"❌ Errore: {e}")

    cur.close()
    conn.close()

# -------------------------------------------------------------------
if __name__ == "__main__":
    main()
