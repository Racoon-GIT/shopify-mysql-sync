import os
import sys
import json
import time
import requests
import mysql.connector

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

TEMP_VARIANTS_TABLE = "temp_variants"

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)

def get_product(pid):
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{pid}.json"
    res = requests.get(url, headers=HEADERS)
    res.raise_for_status()
    return res.json()["product"]

def save_variants_to_db(conn, product):
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {TEMP_VARIANTS_TABLE} (
            Product_id BIGINT,
            Variant_id BIGINT,
            Variant_json JSON,
            PRIMARY KEY (Variant_id)
        )""")
    cur.execute(f"DELETE FROM {TEMP_VARIANTS_TABLE} WHERE Product_id = %s", (product["id"],))

    for v in product["variants"]:
        # rimuovi campi non accettati nel recreate
        v.pop("admin_graphql_api_id", None)
        v.pop("id", None)
        v.pop("product_id", None)
        cur.execute(
            f"INSERT INTO {TEMP_VARIANTS_TABLE} (Product_id, Variant_id, Variant_json) VALUES (%s, %s, %s)",
            (product["id"], v["id"], json.dumps(v))
        )
    conn.commit()
    log(f"💾 Salvate {len(product['variants'])} varianti per il prodotto {product['id']}")

def delete_all_variants(product):
    log(f"🧨 Elimino varianti di {product['id']}…")
    variants = product["variants"]
    if len(variants) <= 1:
        log("⚠️  Solo una variante presente, salto la cancellazione.")
        return

    to_delete = variants[:-1]  # lascia l'ultima variante
    for v in to_delete:
        vid = v["id"]
        url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{vid}.json"
        res = requests.delete(url, headers=HEADERS)
        if res.status_code == 200:
            log(f"🗑️  Variante {vid} eliminata")
        else:
            log(f"❌ Errore eliminando variante {vid}: {res.text}")
    time.sleep(1)

def recreate_variants(conn, product_id):
    cur = conn.cursor()
    cur.execute(
        f"SELECT Variant_json FROM {TEMP_VARIANTS_TABLE} WHERE Product_id = %s",
        (product_id,)
    )
    rows = cur.fetchall()

    for (variant_json,) in rows:
        variant = json.loads(variant_json)
        data = {"variant": variant}
        url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants.json"
        res = requests.post(url, headers=HEADERS, data=json.dumps(data))
        if res.status_code in [200, 201]:
            new_id = res.json()["variant"]["id"]
            log(f"✅ Variante ricreata con ID {new_id}")
        else:
            log(f"❌ Errore creando variante: {res.text}")
        time.sleep(0.5)

def main():
    if len(sys.argv) < 2:
        log("❌ Specificare almeno un product ID separato da virgola")
        sys.exit(1)

    product_ids = [pid.strip() for pid in sys.argv[1].split(",") if pid.strip().isdigit()]
    if not product_ids:
        log("❌ Nessun ID valido")
        sys.exit(1)

    log("🔌 Connessione MySQL…")
    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )

    for pid in product_ids:
        try:
            product = get_product(pid)
            save_variants_to_db(conn, product)
            delete_all_variants(product)
            recreate_variants(conn, int(pid))
        except Exception as e:
            log(f"❌ Errore con il prodotto {pid}: {e}")

    conn.close()
    log("🏁 Fine processo")

if __name__ == "__main__":
    main()
