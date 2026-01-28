#!/usr/bin/env python3
# shopify_to_mysql.py
"""
Sincronizzazione prodotti Shopify -> MySQL via GraphQL.

Funzionalità:
- Recupera tutti i prodotti attivi da Shopify (filtrati per tag specifici)
- Usa GraphQL per efficienza (prodotti + varianti + metafield in una chiamata)
- Sincronizza varianti su tabella MySQL online_products
- Traccia storico variazioni prezzi su price_history
- Rimuove varianti non più presenti su Shopify
- Sincronizza metafield prodotto e variante
- Sincronizza immagini prodotto (JSON)
- Sincronizza body HTML

Ottimizzazione rispetto a REST:
- REST: ~9000+ chiamate API (1 per prodotto metafield + 1 per variante metafield)
- GraphQL: ~75 chiamate (10 prodotti per pagina con tutto incluso, rispetta limite 1000 punti)
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
        product: Dati prodotto Shopify (normalizzato da GraphQL)
        valid_tags: Set di tag validi

    Returns:
        bool: True se il prodotto ha almeno un tag valido
    """
    tags_raw = product.get("tags", "")
    tags = [t.strip().lower() for t in tags_raw.split(",")]
    return any(tag in valid_tags for tag in tags)


def sync_products_graphql(config: Config, client: ShopifyClient, db: Database) -> None:
    """
    Esegue la sincronizzazione completa dei prodotti usando GraphQL.
    Molto più efficiente di REST perché recupera tutto in una query.

    Args:
        config: Configurazione
        client: Client Shopify
        db: Connessione database
    """
    # Inizializza tabelle
    db.init_sync_tables()

    # Recupera varianti esistenti
    existing_ids = db.get_existing_variant_ids()

    # Costruisce mappa collezioni (ancora via REST, ma sono poche chiamate)
    collection_map = client.build_product_collections_map()

    # Contatori
    product_count = 0
    tot_ins = 0
    tot_upd = 0
    seen_ids = set()

    log("🚀 Avvio sincronizzazione via GraphQL...")
    log("📡 Recupero prodotti con varianti, metafield e inventory inclusi...")

    # Usa GraphQL per recuperare tutto in una volta
    # La location "Magazzino" è gestita direttamente nella query GraphQL
    for product in client.get_products_graphql(status="active", location_name="Magazzino"):
        # Filtro per tag
        if not is_shoe(product, config.VALID_TAGS):
            continue

        product_id = product["id"]
        tags_string = product.get("tags", "")
        collections = ", ".join(collection_map.get(product_id, []))

        # Body HTML del prodotto
        body_html = product.get("body_html")

        # Immagini prodotto (JSON) - usa il metodo esistente
        product_images_json = ShopifyClient.build_images_json(product)

        # Metafield prodotto (già inclusi nella risposta GraphQL)
        raw_product_mf = product.get("metafields", {})
        product_mf = ShopifyClient.extract_product_metafields(raw_product_mf)

        ins_count = 0
        upd_count = 0

        for variant in product.get("variants", []):
            vid = variant["id"]
            seen_ids.add(vid)

            price = Decimal(variant["price"] or "0")
            compare = Decimal(variant["compare_at_price"] or "0")

            # Stock Magazzino (già incluso nella risposta GraphQL)
            inventory_item_id = variant.get("inventory_item_id", 0)
            stock_magazzino = variant.get("stock_for_location")

            # Metafield variante (già inclusi nella risposta GraphQL)
            raw_variant_mf = variant.get("metafields", {})
            variant_mf = ShopifyClient.extract_variant_metafields(raw_variant_mf)

            # Verifica se esiste e se i prezzi sono cambiati
            existing = db.get_variant_prices(vid)
            if existing:
                old_price, old_cmp = existing
                if old_price != price or old_cmp != compare:
                    db.insert_price_history(vid, old_price, price, old_cmp, compare)
                upd_count += 1
            else:
                ins_count += 1

            # Upsert con tutti i campi
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

        # Commit per prodotto
        db.commit()
        tot_ins += ins_count
        tot_upd += upd_count

        # Log periodico
        product_count += 1
        if config.debug and product_count % 50 == 0:
            log(f"[Prodotti elaborati: {product_count}] ➕ Insert: {tot_ins} | ↺ Update: {tot_upd}")

    log(f"📦 Elaborati {product_count} prodotti filtrati")

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
            # Usa GraphQL per efficienza massima
            sync_products_graphql(config, client, db)

    except Exception as exc:
        log(f"❌ Errore fatale: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
