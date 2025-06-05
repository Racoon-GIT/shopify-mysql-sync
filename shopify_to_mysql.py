import requests
import mysql.connector
import os
from urllib.parse import urlparse, parse_qs

SHOP_DOMAIN = os.getenv("SHOPIFY_DOMAIN")
ACCESS_TOKEN = os.getenv("SHOPIFY_TOKEN")
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_NAME = os.getenv("DB_NAME")

API_VERSION = "2024-04"

headers = {
    "X-Shopify-Access-Token": ACCESS_TOKEN,
    "Content-Type": "application/json"
}

def log(message):
    print(message)

def drop_and_create_table(cursor):
    log("💣 DROP + CREATE della tabella online_products...")
    cursor.execute("DROP TABLE IF EXISTS online_products")
    cursor.execute("""
        CREATE TABLE online_products (
            Variant_id BIGINT PRIMARY KEY,
            Variant_Title TEXT,
            SKU VARCHAR(255),
            Barcode VARCHAR(255),
            Product_id BIGINT,
            Product_title TEXT,
            Product_handle VARCHAR(255),
            Vendor VARCHAR(255),
            Price VARCHAR(255),
            Compare_AT_Price VARCHAR(255)
        )
    """)

def process_and_store(product, cursor, inserted_ids):
    insert_sql = """
        INSERT INTO online_products (
            Variant_id, Variant_Title, SKU, Barcode,
            Product_id, Product_title, Product_handle, Vendor,
            Price, Compare_AT_Price
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    inserted_count, duplicate_count = 0, 0

    for variant in product.get("variants", []):
        variant_id = variant["id"]
        if variant_id in inserted_ids:
            duplicate_count += 1
            continue
        inserted_ids.add(variant_id)
        row = (
            variant_id,
            variant["title"],
            variant["sku"],
            variant["barcode"],
            product["id"],
            product["title"],
            product["handle"],
            product["vendor"],
            variant["price"],
            variant["compare_at_price"]
        )
        cursor.execute(insert_sql, row)
        inserted_count += 1

    return inserted_count, duplicate_count

def extract_next_page_info(link_header):
    if not link_header:
        return None
    parts = link_header.split(",")
    for part in parts:
        if 'rel="next"' in part:
            url = part.split(";")[0].strip("<> ")
            parsed_url = urlparse(url)
            query_params = parse_qs(parsed_url.query)
            return query_params.get("page_info", [None])[0]
    return None

def main():
    log("🛢️ Connessione a MySQL...")
    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )
    log(f"✅ Connesso al database: {conn.database}")
    cursor = conn.cursor()

    drop_and_create_table(cursor)
    conn.commit()

    log("📦 Connessione a Shopify...")
    base_url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?status=active&limit=250"
    next_page_info = None
    total_inserted = 0
    total_duplicates = 0
    inserted_ids = set()
    page_count = 1

    while True:
        
        if next_page_info:
            url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?page_info={next_page_info}"
        else:
            url = base_url  # include ?status=active&limit=250
            
        log(f"🌐 Pagina {page_count}: {url}")
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        batch = res.json().get("products", [])

        page_inserted, page_duplicates = 0, 0

        for product in batch:
            ins, dup = process_and_store(product, cursor, inserted_ids)
            page_inserted += ins
            page_duplicates += dup

        total_inserted += page_inserted
        total_duplicates += page_duplicates
        log(f"✅ Inserite in questa pagina: {page_inserted}")
        log(f"⚠️ Duplicati ignorati in questa pagina: {page_duplicates}")
        log(f"📦 Varianti totali finora: {len(inserted_ids)}")

        conn.commit()
        next_page_info = extract_next_page_info(res.headers.get("Link"))

        if not next_page_info:
            break

        page_count += 1

    cursor.close()
    conn.close()
    log(f"🏁 Fine script. Varianti inserite: {total_inserted} | Duplicati ignorati: {total_duplicates}")

if __name__ == "__main__":
    main()
