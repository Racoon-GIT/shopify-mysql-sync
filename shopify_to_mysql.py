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

def fetch_products():
    url = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products.json?status=active&limit=250"
    products = []
    while url:
        res = requests.get(url, headers=headers)
        res.raise_for_status()
        batch = res.json().get("products", [])
        products.extend(batch)

        link = res.headers.get("Link")
        if link and 'rel="next"' in link:
            url = link.split(";")[0].strip("<>")
        else:
            url = None
    return products

def main():
    print("📦 Connessione a Shopify...")
    products = fetch_products()

    print("🛢️ Connessione a MySQL...")
    conn = mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )
    cursor = conn.cursor()

    print("🧹 Cancellazione della tabella online_products...")
    cursor.execute("DELETE FROM online_products")

    print("✏️ Inserimento varianti...")
    insert_sql = """
        INSERT INTO online_products (
            Variant_id, Variant_Title, SKU, Barcode,
            Product_id, Product_title, Product_handle, Vendor,
            Price, Compare_AT_Price
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """

    for product in products:
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

    conn.commit()
    cursor.close()
    conn.close()
    print(f"✅ Completato! Totale varianti inserite: {sum(len(p['variants']) for p in products)}")

if __name__ == "__main__":
    main()
