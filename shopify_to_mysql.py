#!/usr/bin/env python3
# shopify_to_mysql.py
"""
Sincronizzazione prodotti Shopify -> MySQL.

Funzionalità:
- Recupera tutti i prodotti attivi da Shopify (filtrati per tag specifici)
- Sincronizza varianti su tabella MySQL online_products
- Traccia storico variazioni prezzi su price_history
- Rimuove varianti non più presenti su Shopify
- Sincronizza metafield prodotto e variante
- Sincronizza immagini prodotto (JSON)
- Sincronizza body HTML
"""

import sys
from decimal import Decimal
from typing import Dict, Any

from src.config import Config, log
from src.shopify_client import ShopifyClient
from src.db import Database


def is_shoe(product: dict, valid_tags: set) -> bool:
    """
    Verifica se il prodotto è una calzatura in base ai tag.

    Args:
        product: Dati prodotto Shopify
        valid_tags: Set di tag validi

    Returns:
        bool: True se il prodotto ha almeno un tag valido
    """
    tags_raw = product.get("tags", "")
    tags = [t.strip().lower() for t in tags_raw.split(",")]
    return any(tag in valid_tags for tag in tags)


def sync_products(config: Config, client: ShopifyClient, db: Database) -> None:
    """
    Esegue la sincronizzazione completa dei prodotti.

    Args:
        config: Configurazione
        client: Client Shopify
        db: Connessione database
    """
    # Inizializza tabelle
    db.init_sync_tables()

    # Recupera varianti esistenti
    existing_ids = db.get_existing_variant_ids()

    # Costruisce mappa collezioni
    collection_map = client.build_product_collections_map()

    # Recupera ID location "Magazzino"
    log("📍 Recupero ID location 'Magazzino'...")
    magazzino_location_id = client.get_location_id_by_name("Magazzino")
    if magazzino_location_id:
        log(f"✅ Location 'Magazzino' trovata: ID {magazzino_location_id}")
    else:
        log("⚠️ Location 'Magazzino' non trovata - Stock_Magazzino sarà NULL")

    # Contatori
    page = 0
    tot_ins = 0
    tot_upd = 0
    seen_ids = set()

    # Raccogli prima tutti i prodotti filtrati per recuperare inventory in batch
    log("🔄 Raccolta prodotti e varianti...")
    products_to_sync = []

    for product in client.get_products(status="active"):
        # Filtro per tag
        if not is_shoe(product, config.VALID_TAGS):
            continue
        products_to_sync.append(product)

    log(f"📦 Trovati {len(products_to_sync)} prodotti da sincronizzare")

    # Raccogli tutti gli inventory_item_id per batch query
    all_inventory_item_ids = []
    for product in products_to_sync:
        for variant in product.get("variants", []):
            inv_id = variant.get("inventory_item_id")
            if inv_id:
                all_inventory_item_ids.append(inv_id)

    # Recupera inventory per location Magazzino in batch
    inventory_map = {}
    if magazzino_location_id and all_inventory_item_ids:
        log(f"📊 Recupero stock Magazzino per {len(all_inventory_item_ids)} varianti...")
        inventory_map = client.build_inventory_map_for_location(
            all_inventory_item_ids,
            magazzino_location_id
        )
        log(f"✅ Stock recuperato per {len([v for v in inventory_map.values() if v is not None])} varianti")

    # Cache per metafield prodotto (per non richiederli per ogni variante)
    product_metafields_cache: Dict[int, Dict[str, Any]] = {}
    # Cache per metafield variante
    variant_metafields_cache: Dict[int, Dict[str, Any]] = {}

    log("🏷️ Inizio sincronizzazione con metafield...")

    # Ora sincronizza i prodotti
    for product in products_to_sync:
        product_id = product["id"]
        tags_string = product.get("tags", "")
        collections = ", ".join(collection_map.get(product_id, []))

        # Body HTML del prodotto
        body_html = product.get("body_html")

        # Immagini prodotto (JSON)
        product_images_json = ShopifyClient.build_images_json(product)

        # Recupera metafield prodotto (una volta per prodotto)
        if product_id not in product_metafields_cache:
            raw_mf = client.get_product_metafields(product_id)
            product_metafields_cache[product_id] = ShopifyClient.extract_product_metafields(raw_mf)

        product_mf = product_metafields_cache[product_id]

        ins_count = 0
        upd_count = 0

        for variant in product.get("variants", []):
            vid = variant["id"]
            seen_ids.add(vid)

            price = Decimal(variant["price"] or "0")
            compare = Decimal(variant["compare_at_price"] or "0")

            # Recupera stock Magazzino dalla mappa
            inventory_item_id = variant.get("inventory_item_id", 0)
            stock_magazzino = inventory_map.get(inventory_item_id)

            # Recupera metafield variante
            if vid not in variant_metafields_cache:
                raw_vmf = client.get_variant_metafields(vid)
                variant_metafields_cache[vid] = ShopifyClient.extract_variant_metafields(raw_vmf)

            variant_mf = variant_metafields_cache[vid]

            # Verifica se esiste e se i prezzi sono cambiati
            existing = db.get_variant_prices(vid)
            if existing:
                old_price, old_cmp = existing
                if old_price != price or old_cmp != compare:
                    db.insert_price_history(vid, old_price, price, old_cmp, compare)
                upd_count += 1
            else:
                ins_count += 1

            # Upsert con tutti i nuovi campi
            db.upsert_product(
                variant_id=vid,
                variant_title=variant.get("title", ""),
                sku=variant.get("sku", ""),
                barcode=variant.get("barcode", ""),
                product_id=product_id,
                product_title=product.get("title", ""),
                product_handle=product.get("handle", ""),
                vendor=product.get("vendor", ""),
                price=price,
                compare_at_price=compare,
                inventory_item_id=inventory_item_id,
                stock_magazzino=stock_magazzino,
                tags=tags_string,
                collections=collections,
                # Nuovi campi
                body_html=body_html,
                product_images=product_images_json,
                # Metafield Prodotto
                mf_customization_description=product_mf.get("customization_description"),
                mf_shoe_details=product_mf.get("shoe_details"),
                mf_customization_details=product_mf.get("customization_details"),
                mf_o_description=product_mf.get("o_description"),
                mf_handling=product_mf.get("handling"),
                mf_google_custom_product=product_mf.get("google_custom_product"),
                # Metafield Variante (Google Shopping)
                mf_google_age_group=variant_mf.get("google_age_group"),
                mf_google_condition=variant_mf.get("google_condition"),
                mf_google_gender=variant_mf.get("google_gender"),
                mf_google_mpn=variant_mf.get("google_mpn"),
                mf_google_custom_label_0=variant_mf.get("google_custom_label_0"),
                mf_google_custom_label_1=variant_mf.get("google_custom_label_1"),
                mf_google_custom_label_2=variant_mf.get("google_custom_label_2"),
                mf_google_custom_label_3=variant_mf.get("google_custom_label_3"),
                mf_google_custom_label_4=variant_mf.get("google_custom_label_4"),
                mf_google_size_system=variant_mf.get("google_size_system"),
                mf_google_size_type=variant_mf.get("google_size_type"),
            )

        # Commit per prodotto (ottimizzato rispetto a singola variante)
        db.commit()
        tot_ins += ins_count
        tot_upd += upd_count

        # Log periodico
        page += 1
        if config.debug and page % 10 == 0:
            log(f"[Prodotti elaborati: {page}] ➕ Insert: {tot_ins} | ↺ Update: {tot_upd}")

    # Rimozione varianti scomparse
    to_delete = existing_ids - seen_ids
    if to_delete:
        deleted = db.delete_variants(to_delete)
        log(f"🗑️  Rimossi {deleted} varianti non più su Shopify")

    db.commit()
    log(f"✅ Sync completato. ➕ {tot_ins} insert | ↺ {tot_upd} update | Totale attuale: {len(seen_ids)}")


def main() -> None:
    """Entry point principale."""
    try:
        # Carica configurazione (product_ids non richiesto per sync)
        config = Config.from_env(require_product_ids=False)

        # Inizializza client e database
        client = ShopifyClient(config)

        with Database(config) as db:
            sync_products(config, client, db)

    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
