#!/usr/bin/env python3
# shopify_to_mysql.py
# --------------------------------------------------
# Sincronizza incrementale le varianti SCARPE da Shopify a MySQL
# • Esclude “Outlet”
# • Upsert su online_products (DECIMAL prezzi)
# • Rimuove righe assenti su Shopify
# • Logga cambi prezzo in price_history
# --------------------------------------------------

import os, sys, time
from decimal import Decimal
import requests, mysql.connector

DEBUG = False    # ⇦ imposta a True se vuoi i contatori per pagina

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

# ---------- DB SETUP ----------------------------------------------
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
  Inventory_Item_ID BIGINT
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

# ---------- FILTRO SCARPE -----------------------------------------
SHOE_KW = {
    "shoe","shoes","footwear",
    "sneaker","sneakers",
    "boot","boots",
    "scarpa","scarpe",
    "stivale","stivali",
    "sandal","sandals"
}
def is_shoe(product: dict) -> bool:
    ptype = (product.get("product_type") or "").lower()
    pcat  = ""
    pc_obj = product.get("product_category")
    if isinstance(pc_obj, dict):
        pcat = (pc_obj.get("path") or "").lower()
    tags  = (product.get("tags") or "").replace(",", " ").lower()
    blob  = f"{ptype} {pcat} {tags}"
    return any(k in blob for k in SHOE_KW)

# ---------- UTILS --------------------------------------------------
def extract_next(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip("<> ")
    return None

# ---------- MAIN ---------------------------------------------------
def main() -> None:
    # DB connect & DDL
    log("🔌 Connessione MySQL…")
    conn = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cur  = conn.cursor()
    cur.execute(DDL_ONLINE_PRODUCTS)
    cur.execute(DDL_PRICE_HISTORY)
    conn.commit()

    # Pre-carica set ID esistenti per cancellazione finale
    cur.execute("SELECT Variant_id FROM online_products")
    existing_ids = {row[0] for row in cur.fetchall()}

    base_url = (
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}"
        "/products.json?status=active&limit=250"
    )
    next_url = None
    page = tot_ins = 0
    seen_ids: set[int] = set()

    while True:
        url = next_url or base_url
        page += 1
        res = requests.get(url, headers=HEADERS); res.raise_for_status()
        prods = res.json().get("products", [])

        ins_page = upd_page = filtered_ns = filtered_out = 0

        for p in prods:
            if not is_shoe(p):
                filtered_ns += 1
                continue
            if "outlet" in p["title"].lower():
                filtered_out += 1
                continue

            for v in p["variants"]:
                vid = v["id"]
                seen_ids.add(vid)

                price     = Decimal(v["price"] or "0")
                compare   = Decimal(v["compare_at_price"] or "0")
                # Price change check
                cur.execute(
                    "SELECT Price, Compare_AT_Price FROM online_products WHERE Variant_id=%s",
                    (vid,)
                )
                row = cur.fetchone()
                if row:
                    old_price, old_cmp = row
                    if old_price != price or old_cmp != compare:
                        cur.execute(
                            "INSERT INTO price_history"
                            " (Variant_id, Old_Price, New_Price, Old_Compare_AT, New_Compare_AT)"
                            " VALUES (%s,%s,%s,%s,%s)",
                            (vid, old_price, price, old_cmp, compare)
                        )
                    upd_page += 1
                else:
                    ins_page += 1

                # Upsert
                cur.execute("""
                    INSERT INTO online_products
                    (Variant_id, Variant_Title, SKU, Barcode,
                     Product_id, Product_title, Product_handle, Vendor,
                     Price, Compare_AT_Price, Inventory_Item_ID)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                       Inventory_Item_ID=VALUES(Inventory_Item_ID)
                """, (
                    vid, v["title"], v["sku"], v["barcode"],
                    p["id"], p["title"], p["handle"], p["vendor"],
                    price, compare, v["inventory_item_id"]
                ))

        if DEBUG:
            log(f"[P{page}] +{ins_page} new | ↺ {upd_page} upd | "
                f"🚫NS {filtered_ns} | 🚫OUT {filtered_out}")
        tot_ins += ins_page
        conn.commit()

        next_url = extract_next(res.headers.get("Link"))
        if not next_url:
            break

    # Delete variants that disappeared from Shopify
    to_delete = existing_ids - seen_ids
    if to_delete:
        cur.execute(
            f"DELETE FROM online_products WHERE Variant_id IN ({','.join(['%s']*len(to_delete))})",
            tuple(to_delete)
        )
        log(f"🗑️  Rimossi {cur.rowcount} varianti non più presenti su Shopify")

    conn.commit()
    cur.close(); conn.close()
    log(f"✅ Sync concluso. Insert: {tot_ins} | Upd: {len(seen_ids)-tot_ins} "
        f"| Totale varianti in tabella: {len(seen_ids)}")

# -------------------------------------------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)
