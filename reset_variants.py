import os
import sys
import time
import json
import requests
import mysql.connector
from mysql.connector import Error

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

# ---------- UTILS --------------------------------------------------
def safe_request(method, url, headers=None, json=None, max_retries=5):
    for attempt in range(max_retries):
        res = requests.request(method, url, headers=headers, json=json)
        if res.status_code == 429:
            wait = 2 ** attempt
            log(f"⚠️ Rate limit, attendo {wait}s...")
            time.sleep(wait)
            continue
        if res.status_code >= 400:
            try:
                error_detail = res.json()
                log(f"❌ Errore API {res.status_code}: {json.dumps(error_detail, indent=2)}")
            except:
                log(f"❌ Errore API {res.status_code}: {res.text}")
        res.raise_for_status()
        return res
    raise Exception("❌ Troppe richieste, impossibile continuare")

# ---------- INVENTORY MANAGEMENT -----------------------------------
def get_inventory_levels(inventory_item_id):
    """Recupera tutti gli inventory levels per un inventory_item_id"""
    try:
        res = safe_request(
            "GET",
            f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/inventory_levels.json?inventory_item_ids={inventory_item_id}",
            headers=HEADERS
        )
        levels = res.json().get("inventory_levels", [])
        log(f"  📍 Trovati {len(levels)} inventory levels per item {inventory_item_id}")
        return levels
    except Exception as e:
        log(f"⚠️ Errore recupero inventory levels: {e}")
        return []

def set_inventory_level(inventory_item_id, location_id, available):
    """Imposta l'inventory level per una location specifica"""
    try:
        payload = {
            "location_id": location_id,
            "inventory_item_id": inventory_item_id,
            "available": available
        }
        safe_request(
            "POST",
            f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/inventory_levels/set.json",
            headers=HEADERS,
            json=payload
        )
        log(f"  ✅ Inventory impostato: location {location_id} → {available} unità")
        time.sleep(0.6)
        return True
    except Exception as e:
        log(f"  ❌ Errore impostazione inventory: {e}")
        return False

# ---------- DB ------------------------------------------------------
def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )

def ensure_temp_tables(cur):
    # Tabella backup varianti
    cur.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS variant_backup (
            id BIGINT,
            product_id BIGINT,
            inventory_item_id BIGINT,
            variant_json TEXT,
            PRIMARY KEY (product_id, id)
        )
    """)
    cur.execute("DELETE FROM variant_backup")
    
    # Tabella backup inventory levels
    cur.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS inventory_backup (
            variant_id BIGINT,
            inventory_item_id BIGINT,
            location_id BIGINT,
            available INT,
            PRIMARY KEY (variant_id, location_id)
        )
    """)
    cur.execute("DELETE FROM inventory_backup")

# ---------- MAIN ----------------------------------------------------
def main():
    product_ids_env = os.getenv("PRODUCT_IDS")
    if not product_ids_env:
        log("❌ Variabile d'ambiente PRODUCT_IDS non definita")
        sys.exit(1)

    product_ids = [pid.strip() for pid in product_ids_env.split(",") if pid.strip()]
    db = connect_db()
    cur = db.cursor()
    ensure_temp_tables(cur)

    for product_id in product_ids:
        log(f"📦 Elaborazione prodotto: {product_id}")

        # ===== STEP 1: FETCH VARIANTI =====
        try:
            res = safe_request(
                "GET",
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
                headers=HEADERS
            )
            variants = res.json().get("variants", [])
            log(f"🔍 Trovate {len(variants)} varianti")
        except Exception as e:
            log(f"❌ Errore durante l'accesso alle varianti: {e}")
            continue

        # ===== STEP 2: BACKUP VARIANTI + INVENTORY =====
        log(f"💾 Backup varianti e inventory levels...")
        for v in variants:
            # Backup dati variante
            cur.execute(
                "INSERT INTO variant_backup (id, product_id, inventory_item_id, variant_json) VALUES (%s, %s, %s, %s)",
                (v["id"], product_id, v.get("inventory_item_id"), json.dumps(v))
            )
            
            # Backup inventory levels (solo se gestito)
            if v.get("inventory_management") and v.get("inventory_item_id"):
                inventory_levels = get_inventory_levels(v["inventory_item_id"])
                for level in inventory_levels:
                    cur.execute(
                        "INSERT INTO inventory_backup (variant_id, inventory_item_id, location_id, available) VALUES (%s, %s, %s, %s)",
                        (v["id"], v["inventory_item_id"], level["location_id"], level["available"])
                    )
                    log(f"  💾 Backup inventory: variant {v['id']}, location {level['location_id']}, qty {level['available']}")
        
        db.commit()

        # ===== STEP 3: RINOMINA PRIMA VARIANTE COME DUMMY =====
        if variants:
            dummy_id = variants[0]["id"]
            try:
                safe_request(
                    "PUT",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
                    headers=HEADERS,
                    json={"variant": {"id": dummy_id, "option1": "DUMMY_TEMP_VARIANT"}}
                )
                log("✏️ Variante DUMMY creata")
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore dummy update: {e}")

        # ===== STEP 4: CANCELLA TUTTE LE VARIANTI TRANNE DUMMY =====
        for v in variants[1:]:
            try:
                safe_request(
                    "DELETE",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{v['id']}.json",
                    headers=HEADERS
                )
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore delete variante {v['id']}: {e}")

        log("🗑️ Varianti eliminate (eccetto dummy)")

        # ===== STEP 5: RICREA VARIANTI DA BACKUP =====
        cur.execute("""
            SELECT id, inventory_item_id, variant_json 
            FROM variant_backup 
            WHERE product_id = %s
        """, (product_id,))
        rows = cur.fetchall()
        
        # Mappa per tracciare old_variant_id -> new_inventory_item_id
        variant_mapping = {}
        
        for (old_variant_id, old_inventory_item_id, variant_json) in rows:
            v = json.loads(variant_json)
            
            # Salta la DUMMY
            if v["option1"] == "DUMMY_TEMP_VARIANT":
                continue
            
            log(f"🔄 Ricreo variante: {v.get('option1')} / {v.get('option2')} / {v.get('option3')}")
            
            # Payload per creare la variante
            payload = {"variant": {
                "option1": v["option1"],
                "option2": v.get("option2"),
                "option3": v.get("option3"),
                "price": v.get("price"),
                "compare_at_price": v.get("compare_at_price"),
                "sku": v.get("sku"),
                "barcode": v.get("barcode"),
                "inventory_management": v.get("inventory_management"),
                "inventory_policy": v.get("inventory_policy"),
                "fulfillment_service": v.get("fulfillment_service"),
                "requires_shipping": v.get("requires_shipping", True),
                "taxable": v.get("taxable", True),
                "weight": v.get("weight", 0),
                "weight_unit": v.get("weight_unit", "kg")
            }}
            
            try:
                res = safe_request(
                    "POST",
                    f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/products/{product_id}/variants.json",
                    headers=HEADERS,
                    json=payload
                )
                new_variant = res.json().get("variant", {})
                new_inventory_item_id = new_variant.get("inventory_item_id")
                
                if new_inventory_item_id:
                    variant_mapping[old_variant_id] = new_inventory_item_id
                    log(f"  ✅ Variante ricreata, nuovo inventory_item_id: {new_inventory_item_id}")
                
                time.sleep(0.6)
            except Exception as e:
                log(f"❌ Errore creazione variante: {e}")
                continue

        # ===== STEP 6: RIPRISTINA INVENTORY LEVELS =====
        log("📍 Ripristino inventory levels...")
        cur.execute("""
            SELECT variant_id, location_id, available 
            FROM inventory_backup 
            WHERE variant_id IN (SELECT id FROM variant_backup WHERE product_id = %s)
        """, (product_id,))
        
        inventory_rows = cur.fetchall()
        for (old_variant_id, location_id, available) in inventory_rows:
            new_inventory_item_id = variant_mapping.get(old_variant_id)
            if new_inventory_item_id:
                log(f"  🔄 Ripristino inventory: location {location_id}, qty {available}")
                set_inventory_level(new_inventory_item_id, location_id, available)
            else:
                log(f"  ⚠️ Impossibile ripristinare inventory per variant {old_variant_id} (mapping non trovato)")

        # ===== STEP 7: ELIMINA LA DUMMY VARIANT =====
        try:
            safe_request(
                "DELETE",
                f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/variants/{dummy_id}.json",
                headers=HEADERS
            )
            log("✅ Variante DUMMY eliminata")
            time.sleep(0.6)
        except Exception as e:
            log(f"❌ Errore eliminazione dummy: {e}")

        log(f"✅ Prodotto {product_id} completato con successo!\n")

    cur.close()
    db.close()
    log("🎉 Processo completato per tutti i prodotti!")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ Errore fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
