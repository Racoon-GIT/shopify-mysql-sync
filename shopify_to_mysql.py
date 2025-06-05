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

def process_and_store(product, cursor):
    insert_sql = """
        INSERT INTO online_products (
            Variant_id, Variant_Title, SKU, Barcode,
            Product_id, Product_title, Product_handle, Vendor,
            Price, Compare_AT_Price
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    for variant in product.get("variants", []):
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
    cursor = conn.cursor()
    cursor.execute("DELETE FROM online_products")

    print("📦 Connessione a Shopify...")
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?status=active&limit=250"
    total_variants = 0

    while url:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        batch = res.json().get("products", [])

        for product in batch:
            process_and_store(product, cursor)
            total_variants += len(product.get("variants", []))

        # Gestione paginazione
        link = res.headers.get("Link")
        if link and 'rel="next"' in link:
            url = link.split(";")[0].strip("<>")
        else:
            url = None

        conn.commit()  # Commit dopo ogni batch

    cursor.close()
    conn.close()
    print(f"✅ Completato! Totale varianti inserite: {total_variants}")

if __name__ == "__main__":
    main()
