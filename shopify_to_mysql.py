# shopify_to_mysql.py

try:
    import os, sys, time
    from decimal import Decimal
    import requests
    import mysql.connector
    from collections import defaultdict
except Exception as e:
    print(f"❌ Errore fatale in fase di import: {e}", flush=True)
    sys.exit(1)

DEBUG = True

# === VARIABILI D'AMBIENTE ===
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

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

# === TABELLE MYSQL ===
DDL_ONLINE_PRODUCTS = """
CREATE TABLE IF NOT EXISTS online_products (
  Variant_id        BIGINT PRIMARY KEY,
  Variant_Title     TEXT,
  SKU               VARCHAR(255),
  Barcode           VARCHAR(255),
  Product_id        BIGINT,
  Product_title     TEXT,
  Product_handle    VARCHAR(255),
  Vendor            VARCHAR(255),
  Price             DECIMAL(10,2),
  Compare_AT_Price  DECIMAL(10,2),
  Inventory_Item_ID BIGINT,
  Tags              TEXT,
  Collections       TEXT
)
"""

DDL_PRICE_HISTORY = """
CREATE TABLE IF NOT EXISTS price_history (
  id                BIGINT AUTO_INCREMENT PRIMARY KEY,
  Variant_id        BIGINT,
  Old_Price         DECIMAL(10,2),
  New_Price         DECIMAL(10,2),
  Old_Compare_AT    DECIMAL(10,2),
  New_Compare_AT    DECIMAL(10,2),
  changed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

# === FILTRO TAG ===
VALID_TAGS = {
    "sneakers personalizzate",
    "scarpe personalizzate",
    "ciabatte personalizzate",
    "stivali personalizzati"
}

def is_shoe(product: dict) -> bool:
    tags_raw = product.get("tags", "")
    tags = [t.strip().lower() for t in tags_raw.split(",")]
    return any(tag in VALID_TAGS for tag in tags)

# === PAGINAZIONE SHOPIFY ===
def extract_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip("<> ")
    return None

# === CREA MAPPA {product_id: [collection1, collection2]} ===
def build_product_collections_map() -> dict:
    product_to_collections = defaultdict(list)

    def fetch_all_collections(endpoint: str):
        url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/{endpoint}?limit=250"
        while url:
            res = requests.get(url, headers=HEADERS)
            res.raise_for_status()
            collections = res.json().get(endpoint.split(".")[0], [])
            for coll in collections:
                coll_id = coll["id"]
                title = coll["title"]
                # recupera i prodotti nella collezione
                prod_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/collections/{coll_id}/products.json?limit=250"
                while prod_url:
                    r = requests.get(prod_url, headers=HEADERS)
                    r.raise_for_status()
                    for p in r.json().get("products", []):
                        product_to_collections[p["id"]].append(title)
                    prod_url = extract_next(r.headers.get("Link"))
            url = extract_next(res.headers.get("Link"))

    log("🔁 Caricamento mappa collezioni da custom_collections...")
    fetch_all_collections("custom_collections.json")
    log("🔁 Caricamento mappa collezioni da smart_collections...")
    fetch_all_collections("smart_collections.json")
    log(f"✅ Mappa collezioni creata con {len(product_to_collections)} prodotti.")
    return product_to_collections

# === MAIN ===
def main():
    log("🔌 Connessione a MySQL…")
    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cur = conn.cursor()
    cur.execute(DDL_ONLINE_PRODUCTS)
    cur.execute(DDL_PRICE_HISTORY)
    conn.commit()

    cur.execute("SELECT Variant_id FROM online_products")
    existing_ids = {row[0] for row in cur.fetchall()}

    # Costruisci la mappa collezioni
    collection_map = build_product_collections_map()

    base_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?status=active&limit=250"
    next_url = None
    page = tot_ins = tot_upd = 0
    seen_ids = set()

    while True:
        url = next_url or base_url
        page += 1
        res = requests.get(url, headers=HEADERS)
        res.raise_for_status()
        products = res.json().get("products", [])

        ins_page = upd_page = 0

        for p in products:
            if not is_shoe(p):
                continue

            tags_string = p.get("tags", "")
            collections = ", ".join(collection_map.get(p["id"], []))

            for v in p["variants"]:
                vid = v["id"]
                seen_ids.add(vid)

                price = Decimal(v["price"] or "0")
                compare = Decimal(v["compare_at_price"] or "0")

                cur.execute("SELECT Price, Compare_AT_Price FROM online_products WHERE Variant_id=%s", (vid,))
                row = cur.fetchone()
                if row:
                    old_price, old_cmp = row
                    if old_price != price or old_cmp != compare:
                        cur.execute(
                            "INSERT INTO price_history (Variant_id, Old_Price, New_Price, Old_Compare_AT, New_Compare_AT) "
                            "VALUES (%s,%s,%s,%s,%s)",
                            (vid, old_price, price, old_cmp, compare)
                        )
                    upd_page += 1
                else:
                    ins_page += 1

                cur.execute("""
                    INSERT INTO online_products (
                      Variant_id, Variant_Title, SKU, Barcode,
                      Product_id, Product_title, Product_handle, Vendor,
                      Price, Compare_AT_Price, Inventory_Item_ID,
                      Tags, Collections
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      Variant_Title=VALUES(Variant_Title),
                      SKU=VALUES(SKU),
                      Barcode=VALUES(Barcode),
                      Product_id=VALUES(Product_id),
                      Product_title=VALUES(Product_title),
                      Product_handle=VALUES(Product_handle),
                      Vendor=VALUES(Vendor),
                      Price=VALUES(Price),
                      Compare_AT_Price=VALUES(Compare_AT_Price),
                      Inventory_Item_ID=VALUES(Inventory_Item_ID),
                      Tags=VALUES(Tags),
                      Collections=VALUES(Collections)
                """, (
                    vid, v["title"], v["sku"], v["barcode"],
                    p["id"], p["title"], p["handle"], p["vendor"],
                    price, compare, v["inventory_item_id"],
                    tags_string, collections
                ))

            conn.commit()
            time.sleep(0.2)

        if DEBUG:
            log(f"[Pagina {page}] ➕ Insert: {ins_page} | ↺ Update: {upd_page}")
        tot_ins += ins_page
        tot_upd += upd_page

        next_url = extract_next(res.headers.get("Link"))
        if not next_url:
            break

    # Rimozione varianti scomparse
    to_delete = existing_ids - seen_ids
    if to_delete:
        cur.execute(
            f"DELETE FROM online_products WHERE Variant_id IN ({','.join(['%s']*len(to_delete))})",
            tuple(to_delete)
        )
        log(f"🗑️  Rimossi {cur.rowcount} varianti non più su Shopify")

    conn.commit()
    cur.close()
    conn.close()
    log(f"✅ Sync completato. ➕ {tot_ins} insert | ↺ {tot_upd} update | Totale attuale: {len(seen_ids)}")

# === ENTRYPOINT ===
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)
