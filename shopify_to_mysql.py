#!/usr/bin/env python3
# shopify_to_mysql.py
# --------------------------------------------------
# Sincronizza tutte le varianti di SCARPE (no “Outlet”)
# da Shopify a MySQL, ricreando la tabella online_products
# --------------------------------------------------

import os, requests, mysql.connector

# ---------- CONFIG -------------------------------------------------
SHOP_DOMAIN  = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST      = os.getenv("DB_HOST")
DB_USER      = os.getenv("DB_USER")
DB_PASS      = os.getenv("DB_PASS")
DB_NAME      = os.getenv("DB_NAME")

API_VERSION  = "2024-04"
LOG_FILE     = "debug_log.txt"

HEADERS = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

# ---------- UTILS --------------------------------------------------
def log(msg: str) -> None:
    print(msg)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(msg + "\n")

def drop_and_create_table(cur) -> None:
    log("💣 DROP + CREATE della tabella online_products...")
    cur.execute("DROP TABLE IF EXISTS online_products")
    cur.execute("""
        CREATE TABLE online_products (
            Variant_id        BIGINT PRIMARY KEY,
            Variant_Title     TEXT,
            SKU               VARCHAR(255),
            Barcode           VARCHAR(255),
            Product_id        BIGINT,
            Product_title     TEXT,
            Product_handle    VARCHAR(255),
            Vendor            VARCHAR(255),
            Price             VARCHAR(255),
            Compare_AT_Price  VARCHAR(255),
            Inventory_Item_ID BIGINT
        )
    """)

# ---------- FILTRO “SOLO SCARPE” -----------------------------------
SHOE_KEYWORDS = {
    "shoe", "shoes", "footwear",
    "sneaker", "sneakers",
    "boot", "boots",
    "scarpa", "scarpe",
    "stivale", "stivali",
    "sandal", "sandals"         # ← ampliato
}

def is_shoe(product: dict) -> bool:
    """
    True se il prodotto è (verosimilmente) una scarpa.
    • product_type
    • product_category.path (Standard Product Taxonomy)
    • tags  (stringa comma-separata)
    """
    ptype = (product.get("product_type") or "").lower()

    # `product_category` è un dict: prendiamo 'path' (non 'full_path')
    pcat  = ""
    pc_obj = product.get("product_category")
    if isinstance(pc_obj, dict):
        pcat = (pc_obj.get("path") or "").lower()

    # Shopify REST → tags è stringa 'tag1, tag2'
    tags_str = (product.get("tags") or "").replace(",", " ").lower()

    blob = f"{ptype} {pcat} {tags_str}"
    return any(kw in blob for kw in SHOE_KEYWORDS)

# ---------- INSERIMENTO VARIANTI -----------------------------------
def process_and_store(product, cur, inserted_ids: set[int]) -> tuple[int, int]:
    sql = """
        INSERT INTO online_products (
          Variant_id, Variant_Title, SKU, Barcode,
          Product_id, Product_title, Product_handle, Vendor,
          Price, Compare_AT_Price, Inventory_Item_ID
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    ins = dup = 0
    for v in product.get("variants", []):
        vid = v["id"]
        if vid in inserted_ids:
            dup += 1
            continue
        inserted_ids.add(vid)
        cur.execute(sql, (
            vid, v["title"], v["sku"], v["barcode"],
            product["id"], product["title"], product["handle"], product["vendor"],
            v["price"], v["compare_at_price"], v["inventory_item_id"]
        ))
        ins += 1
    return ins, dup

def extract_next_page_url(link_header: str | None) -> str | None:
    if not link_header:
        return None
    for part in link_header.split(","):
        if 'rel="next"' in part:
            return part.split(";")[0].strip("<> ")
    return None

# ---------- MAIN ---------------------------------------------------
def main() -> None:
    # 1) MySQL
    log("🛢️ Connessione a MySQL...")
    conn   = mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, database=DB_NAME
    )
    cursor = conn.cursor()
    drop_and_create_table(cursor)
    conn.commit()

    # 2) Shopify paginato
    base_url = (
        f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}"
        "/products.json?status=active&limit=250"
    )
    next_url = None
    inserted_ids: set[int] = set()
    tot_ins = tot_dup = page = 0

    while True:
        url = next_url or base_url
        page += 1
        log(f"🌐 Pagina {page}: {url}")
        res = requests.get(url, headers=HEADERS); res.raise_for_status()
        products = res.json().get("products", [])

        page_ins = page_dup = 0
        filtered_not_shoe = filtered_outlet = 0

        for p in products:
            if not is_shoe(p):
                filtered_not_shoe += 1
                continue
            if "outlet" in p["title"].lower():
                filtered_outlet += 1
                continue
            ins, dup = process_and_store(p, cursor, inserted_ids)
            page_ins += ins; page_dup += dup

        log(
            f"✅ Inserite: {page_ins} | ⚠️ Duplicati: {page_dup} | "
            f"🚫 Non-scarpe: {filtered_not_shoe} | 🚫 Outlet: {filtered_outlet}"
        )

        tot_ins += page_ins; tot_dup += page_dup
        conn.commit()

        next_url = extract_next_page_url(res.headers.get("Link"))
        if not next_url:
            break

    cursor.close(); conn.close()
    log(f"🏁 Fine: Varianti inserite {tot_ins} | Duplicati {tot_dup}")

# -------------------------------------------------------------------
if __name__ == "__main__":
    main()
