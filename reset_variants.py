#!/usr/bin/env python3
# reset_variants.py
# --------------------------------------------------
# Per ogni ID prodotto indicato, esegue:
# 1. Estrae tutte le varianti e le salva su MySQL
# 2. Cancella tutte le varianti
# 3. Le ricrea una per una con gli stessi dati
# --------------------------------------------------

import os, sys, time, requests
import mysql.connector

# --- CONFIG ---------------------------------------------------------
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

# --- LOG ------------------------------------------------------------
def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# --- INPUT MANUALE --------------------------------------------------
# Inserisci qui gli ID prodotto da ripristinare
product_ids = [
    "14950601294156"  # 👈 sostituisci con i tuoi
]

# --- CAMPI VARIANTI DA BACKUP ---------------------------------------
VARIANT_FIELDS = [
    "title", "price", "compare_at_price", "cost", "taxable", "sku", "barcode",
    "inventory_management", "inventory_policy", "inventory_quantity", "weight", "weight_unit",
    "requires_shipping", "harmonized_system_code", "country_code_of_origin", "option1", "option2", "option3",
    "fulfillment_service", "tax_code"
]

# --- DB SETUP TEMPORANEO --------------------------------------------
def connect_db():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )

def save_variants_to_db(product_id, variants):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS backup_variants")
    cur.execute("""
        CREATE TABLE backup_variants (
            product_id BIGINT,
            variant_json JSON
        )
    """)
    for v in variants:
        cur.execute("INSERT INTO backup_variants (product_id, variant_json) VALUES (%s, %s)",
                    (product_id, str(v).replace("'", '"')))
    conn.commit()
    cur.close(); conn.close()

def get_variants_backup(product_id):
    conn = connect_db()
    cur = conn.cursor()
    cur.execute("SELECT variant_json FROM backup_variants WHERE product_id = %s", (product_id,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [eval(row[0]) for row in rows]

# --- SHOPIFY OPERATIONS ---------------------------------------------
def fetch_product(product_id):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}.json"
    res = requests.get(url, headers=HEADERS); res.raise_for_status()
    return res.json()["product"]

def delete_variant(variant_id):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{variant_id}.json"
    res = requests.delete(url, headers=HEADERS)
    return res.status_code == 200

def create_variant(product_id, data):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json"
    res = requests.post(url, headers=HEADERS, json={"variant": data}); res.raise_for_status()
    return res.json()["variant"]

# --- MAIN -----------------------------------------------------------
def main():
    for pid in product_ids:
        log(f"📦 Elaborazione prodotto: {pid}")
        product = fetch_product(pid)
        variants = product["variants"]

        log(f"💾 Backup di {len(variants)} varianti…")
        save_variants_to_db(pid, variants)

        log("🗑️ Eliminazione varianti (eccetto una)…")
        for i, v in enumerate(variants):
            if i == 0:
                log(f"⚠️ Ignorata variante {v['id']} per mantenere il prodotto valido")
                continue
            success = delete_variant(v["id"])
            if not success:
                log(f"❌ Errore eliminazione variante {v['id']}")

        log("🔁 Ricreazione varianti da backup…")
        for i, data in enumerate(get_variants_backup(pid)):
            if i == 0:
                log("🧼 Pulizia dati prima variante (quella non cancellata)")
                update_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{data['id']}.json"
                # ⚠️ facciamo solo un update parziale per sicurezza
                update_fields = {k: data[k] for k in VARIANT_FIELDS if k in data}
                res = requests.put(update_url, headers=HEADERS, json={"variant": update_fields})
                res.raise_for_status()
            else:
                new_data = {k: data[k] for k in VARIANT_FIELDS if k in data}
                create_variant(pid, new_data)

        log(f"✅ Prodotto {pid} completato.\n")

# --------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Errore: {e}")
        sys.exit(1)
