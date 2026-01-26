#!/usr/bin/env python3
# reset_variants.py
"""
Reset e ricreazione varianti prodotti Shopify.

Funzionalità:
- Cancella e ricrea tutte le varianti di un prodotto
- Preserva inventory levels per tutte le location
- Filtra varianti con "perso" nel titolo
- Pulisce location non originali dopo ricreazione

Strategia (compatibile con metafield su option):
1. Backup varianti e inventory in tabelle temporanee MySQL
2. Delete varianti 2-N
3. Recreate varianti 2-N
4. Delete variante 1
5. Recreate variante 1
6. Ripristina inventory levels
7. Cleanup location extra
"""

import sys
import json
from typing import Dict, Optional

from src.config import Config, log
from src.shopify_client import ShopifyClient
from src.db import Database


def backup_variants_and_inventory(
    product_id: str,
    variants: list,
    client: ShopifyClient,
    db: Database
) -> None:
    """
    Esegue backup di varianti e inventory levels.

    Args:
        product_id: ID prodotto
        variants: Lista varianti da Shopify
        client: Client Shopify
        db: Database
    """
    log("💾 Backup varianti e inventory levels...")

    for idx, variant in enumerate(variants):
        # Backup dati variante (JSON completo)
        db.backup_variant(
            variant_id=variant["id"],
            product_id=int(product_id),
            inventory_item_id=variant.get("inventory_item_id"),
            variant_json=json.dumps(variant),
            position=idx
        )

        # Backup inventory levels (solo se gestito)
        if variant.get("inventory_management") and variant.get("inventory_item_id"):
            inventory_levels = client.get_inventory_levels(variant["inventory_item_id"])

            for level in inventory_levels:
                db.backup_inventory(
                    variant_id=variant["id"],
                    inventory_item_id=variant["inventory_item_id"],
                    location_id=level["location_id"],
                    available=level["available"]
                )
                log(f"  💾 Backup inventory: variant {variant['id']}, "
                    f"location {level['location_id']}, qty {level['available']}")

    db.commit()


def delete_variants(
    product_id: str,
    variants: list,
    client: ShopifyClient,
    skip_first: bool = True
) -> None:
    """
    Elimina varianti dal prodotto.

    Args:
        product_id: ID prodotto
        variants: Lista varianti da eliminare
        client: Client Shopify
        skip_first: Se True, salta la prima variante
    """
    variants_to_delete = variants[1:] if skip_first else variants

    for variant in variants_to_delete:
        if client.delete_variant(int(product_id), variant["id"]):
            log(f"  ✅ Cancellata variante {variant['id']} ({variant.get('title')})")
        else:
            log(f"  ❌ Fallita cancellazione variante {variant['id']}")


def create_variant_from_backup(
    product_id: str,
    variant_json: str,
    client: ShopifyClient
) -> Optional[Dict]:
    """
    Crea una variante da backup JSON.

    Args:
        product_id: ID prodotto
        variant_json: JSON della variante originale
        client: Client Shopify

    Returns:
        Dict: Nuova variante creata o None se skipped/errore
    """
    variant = json.loads(variant_json)

    # Filtro: salta varianti con "perso" nel titolo
    title = variant.get("title", "")
    if "perso" in title.lower():
        log(f"  ⏭️ Skip variante con 'perso' nel titolo: {title}")
        return None

    log(f"🔄 Ricreo variante: {variant.get('option1')} / "
        f"{variant.get('option2')} / {variant.get('option3')}")

    # Payload per creazione variante
    variant_data = {
        "option1": variant["option1"],
        "option2": variant.get("option2"),
        "option3": variant.get("option3"),
        "price": variant.get("price"),
        "compare_at_price": variant.get("compare_at_price"),
        "sku": variant.get("sku"),
        "barcode": variant.get("barcode"),
        "inventory_management": variant.get("inventory_management"),
        "inventory_policy": variant.get("inventory_policy"),
        "fulfillment_service": variant.get("fulfillment_service"),
        "requires_shipping": variant.get("requires_shipping", True),
        "taxable": variant.get("taxable", True),
        "weight": variant.get("weight", 0),
        "weight_unit": variant.get("weight_unit", "kg")
    }

    try:
        new_variant = client.create_variant(int(product_id), variant_data)
        if new_variant.get("inventory_item_id"):
            log(f"  ✅ Variante ricreata, nuovo inventory_item_id: "
                f"{new_variant['inventory_item_id']}")
        return new_variant
    except Exception as e:
        log(f"❌ Errore creazione variante: {e}")
        return None


def recreate_variants(
    product_id: str,
    backup_rows: list,
    client: ShopifyClient,
    skip_first: bool = True
) -> Dict[int, int]:
    """
    Ricrea varianti da backup.

    Args:
        product_id: ID prodotto
        backup_rows: Lista tuple (old_id, inventory_item_id, json, position)
        client: Client Shopify
        skip_first: Se True, salta la prima variante

    Returns:
        Dict[int, int]: Mapping {old_variant_id: new_inventory_item_id}
    """
    variant_mapping = {}
    rows_to_process = backup_rows[1:] if skip_first else backup_rows

    for old_variant_id, old_inventory_item_id, variant_json, position in rows_to_process:
        new_variant = create_variant_from_backup(product_id, variant_json, client)

        if new_variant and new_variant.get("inventory_item_id"):
            variant_mapping[old_variant_id] = new_variant["inventory_item_id"]

    return variant_mapping


def restore_inventory_levels(
    product_id: str,
    variant_mapping: Dict[int, int],
    db: Database,
    client: ShopifyClient
) -> None:
    """
    Ripristina inventory levels dalle backup.

    Args:
        product_id: ID prodotto
        variant_mapping: Mapping old_variant_id -> new_inventory_item_id
        db: Database
        client: Client Shopify
    """
    log("📍 Ripristino inventory levels...")

    inventory_backups = db.get_inventory_backups(product_id)

    for old_variant_id, location_id, available in inventory_backups:
        new_inventory_item_id = variant_mapping.get(old_variant_id)

        if new_inventory_item_id:
            log(f"  🔄 Ripristino inventory: location {location_id}, qty {available}")
            client.set_inventory_level(new_inventory_item_id, location_id, available)
        else:
            log(f"  ⚠️ Impossibile ripristinare inventory per variant {old_variant_id} "
                "(variante non ricreata o skippata)")


def cleanup_extra_locations(
    variant_mapping: Dict[int, int],
    db: Database,
    client: ShopifyClient
) -> None:
    """
    Rimuove location extra non presenti nell'originale.

    Args:
        variant_mapping: Mapping old_variant_id -> new_inventory_item_id
        db: Database
        client: Client Shopify
    """
    log("🧹 Pulizia location inventory non utilizzate...")

    for old_variant_id, new_inventory_item_id in variant_mapping.items():
        # Location originali dal backup
        original_locations = db.get_original_locations(old_variant_id)

        # Skip se non aveva inventory management
        if not original_locations:
            log(f"  ⏭️ Skip cleanup per variant {old_variant_id} "
                "(no inventory management nell'originale)")
            continue

        log(f"  🔍 Variant {old_variant_id}: location originali = {original_locations}")

        # Location attuali della nuova variante
        current_levels = client.get_inventory_levels(new_inventory_item_id)

        for level in current_levels:
            current_location_id = level["location_id"]

            if current_location_id not in original_locations:
                log(f"  🗑️ Location {current_location_id} da rimuovere "
                    "(non era nell'originale)")
                client.remove_inventory_level(new_inventory_item_id, current_location_id)
            else:
                log(f"  ✅ Location {current_location_id} mantenuta (era nell'originale)")


def process_product(
    product_id: str,
    client: ShopifyClient,
    db: Database
) -> bool:
    """
    Elabora un singolo prodotto.

    Args:
        product_id: ID prodotto
        client: Client Shopify
        db: Database

    Returns:
        bool: True se completato con successo
    """
    log(f"📦 Elaborazione prodotto: {product_id}")

    # STEP 1: Fetch varianti
    try:
        variants = client.get_product_variants(int(product_id))
        log(f"🔍 Trovate {len(variants)} varianti")
    except Exception as e:
        log(f"❌ Errore durante l'accesso alle varianti: {e}")
        return False

    if not variants:
        log("⚠️ Nessuna variante trovata, skip prodotto")
        return False

    # STEP 2: Backup
    backup_variants_and_inventory(product_id, variants, client, db)

    # STEP 3: Cancella varianti 2-N
    log("🗑️ Cancellazione varianti dalla 2 alla N...")
    delete_variants(product_id, variants, client, skip_first=True)

    # STEP 4: Ricrea varianti 2-N
    log("🔄 Ricreazione varianti dalla 2 alla N...")
    backup_rows = db.get_variant_backups(product_id)
    variant_mapping = recreate_variants(product_id, backup_rows, client, skip_first=True)

    # STEP 5: Cancella prima variante
    first_variant = variants[0]
    log(f"🗑️ Cancellazione prima variante: {first_variant['id']} ({first_variant.get('title')})")
    if client.delete_variant(int(product_id), first_variant["id"]):
        log("  ✅ Prima variante cancellata")
    else:
        log("  ❌ Errore cancellazione prima variante")

    # STEP 6: Ricrea prima variante
    log("🔄 Ricreazione prima variante...")
    if backup_rows:
        first_row = backup_rows[0]
        old_variant_id, old_inventory_item_id, variant_json, position = first_row

        new_variant = create_variant_from_backup(product_id, variant_json, client)
        if new_variant and new_variant.get("inventory_item_id"):
            variant_mapping[old_variant_id] = new_variant["inventory_item_id"]

    # STEP 7: Ripristina inventory
    restore_inventory_levels(product_id, variant_mapping, db, client)

    # STEP 8: Cleanup location extra
    cleanup_extra_locations(variant_mapping, db, client)

    log(f"✅ Prodotto {product_id} completato con successo!\n")
    return True


def main() -> None:
    """Entry point principale."""
    try:
        # Carica configurazione (product_ids obbligatorio)
        config = Config.from_env(require_product_ids=True)

        if not config.product_ids:
            log("❌ Nessun PRODUCT_ID specificato")
            sys.exit(1)

        # Inizializza client
        client = ShopifyClient(config)

        with Database(config) as db:
            # Inizializza tabelle temporanee per backup
            db.init_backup_tables()

            # Elabora ogni prodotto
            for product_id in config.product_ids:
                process_product(product_id, client, db)

        log("🎉 Processo completato per tutti i prodotti!")

    except Exception as e:
        log(f"❌ Errore fatale: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
