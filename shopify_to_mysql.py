import requests
import mysql.connector
import os

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

def drop_and_create_table(cursor):
    print("💣 DROP + CREATE della tabella online_products...")
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
    for variant in product.get("variants", []):
        if variant["id"] in inserted_ids:
            print(f"⚠️ Variante duplicata ignorata: {variant['id']}")
            continue
        inserted_ids.add(variant["id"])
        row = (
            variant["id"],
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

def main():
    print("🛢️ Connessione a MySQL...")
    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )
    print("✅ Connesso al database:", conn.database)
    cursor = conn.cursor()

    drop_and_create_table(cursor)
    conn.commit()

    print("📦 Connessione a Shopify...")
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?status=active&limit=250"
    total_variants = 0
    skipped = 0
    inserted_ids = set()

    while url:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        batch = res.json().get("products", [])

        for product in batch:
            before = len(inserted_ids)
            process_and_store(product, cursor, inserted_ids)
            after = len(inserted_ids)
            total_variants += after - before
            skipped += (len(product.get("variants", [])) - (after - before))

        link = res.headers.get("Link")
        if link and 'rel="next"' in link:
            url = link.split(";")[0].strip("<>")
        else:
            url = None

        conn.commit()

    cursor.close()
    conn.close()
    print(f"✅ Completato! Varianti inserite: {total_variants} | Duplicati ignorati: {skipped}")

if __name__ == "__main__":
    main()
