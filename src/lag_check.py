# src/lag_check.py
"""
Watchdog di sola lettura per il lag Shopify -> online_products (option (d) di
`docs/sync-lag-plan.md`, gate-1 Ale 2026-09-22).

`classify()` e' PURA — nessuna rete, nessun DB — e concentra la distinzione che
conta:

- `lag`  : variante ASSENTE dal mirror, creata DOPO l'ultimo sync — il difetto
           del mandato (creata, venduta, mai mirrorata: sellable e invisibile
           a `SP_SHOP_ORDER_IN`).
- `drift`: variante assente creata PRIMA dell'ultimo sync (il sync ha avuto
           tempo di assorbirla e non l'ha fatto), oppure presente con prezzo
           diverso e `updatedAt` prima dell'ultimo sync. Un mismatch con
           `updatedAt` DOPO l'ultimo sync e' lag atteso, non drift: non va
           riportato.

Stock non viene mai confrontato (`available` cambia a ogni vendita e il mirror
lo insegue fisiologicamente fino alle 03:04) — solo il prezzo, che cambia solo
per azione umana.

`run_lag_check()` e' l'orchestratore sottile: chiama Shopify/DB (entrambi
iniettabili per i test), applica `classify()`, arricchisce con `real_stock` /
`oversell` e costruisce la busta di risposta di `/api/lag-check`.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from .config import VALID_TAGS, log
from .db import Database
from .shopify_client import ShopifyClient

ROME_TZ = ZoneInfo("Europe/Rome")

DEFAULT_WINDOW_HOURS = 48
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 168
DEFAULT_MAX_PAGES = 20

# Tetto sul numero di entry (lag + drift, combinate) SERIALIZZATE nel body
# come array "lag"/"drift". lag_count/drift_count e oversell_count restano i
# totali VERI (l'allarme), calcolati su TUTTE le entry — mai sul campione.
# Amendment 4, 2026-09-22: get_real_stock_bulk() (src/db.py) arricchisce
# TUTTE le entry in query chunked per sku, non piu' una get_real_stock() per
# entry, quindi il cap non serve piu' a limitare i round-trip DB — solo la
# dimensione del body HTTP. Amendment 3, 2026-09-22: senza questo cap un
# drift storm (mirror lookup vuoto/quasi vuoto) puo' generare fino a 10.000
# entry serializzate in un'unica risposta di /api/lag-check.
MAX_REPORTED_ENTRIES = 50


# --- Helper puri ---

def _isoformat_utc(dt: Optional[datetime]) -> Optional[str]:
    """Formatta un datetime timezone-aware come ISO8601 UTC con suffisso Z."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parsa una stringa ISO8601 Shopify (`...Z`) in datetime UTC-aware."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Converte un prezzo (str/Decimal/numero) in Decimal, None se non valido."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _is_included(variant: Dict[str, Any], valid_tags: frozenset) -> bool:
    """
    Regola di inclusione: prodotto ACTIVE, almeno un tag di VALID_TAGS,
    Product_Title che non contiene 'utlet' (case-insensitive) — mirror del
    filtro outlet della SP. Tutto il resto e' ignorato, non riportato.
    """
    status = (variant.get("product_status") or "").upper()
    if status != "ACTIVE":
        return False

    tags = variant.get("product_tags") or []
    tags_lower = {str(t).strip().lower() for t in tags}
    if not tags_lower & valid_tags:
        return False

    title = variant.get("product_title") or ""
    if "utlet" in title.lower():
        return False

    return True


def classify(
    shopify_variants: List[Dict[str, Any]],
    mirror_by_variant_id: Dict[int, Dict[str, Any]],
    last_sync_utc: Optional[datetime],
    valid_tags: frozenset = VALID_TAGS,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Funzione PURA. Classifica ogni variante Shopify (post-filtro d'inclusione)
    come `lag`, `drift` o silenziosamente OK.

    Args:
        shopify_variants: lista piatta di dict, uno per variante, con chiavi
            variant_id, sku, size, price, created_at (datetime|None),
            updated_at (datetime|None), available (int|None), product_id,
            product_title, product_status, product_tags (list[str]).
        mirror_by_variant_id: variant_id -> {sku, variant_title, price, ...}
            (righe presenti nel mirror `online_products`).
        last_sync_utc: istante UTC dell'ultimo sync (osservato o assunto).
        valid_tags: set di tag validi (default VALID_TAGS di config.py).

    Returns:
        Dict con chiavi "lag" e "drift", ciascuna lista di entry SENZA
        real_stock/oversell (arricchite dal chiamante impuro, che sa parlare
        col DB).
    """
    lag: List[Dict[str, Any]] = []
    drift: List[Dict[str, Any]] = []

    for variant in shopify_variants:
        if not _is_included(variant, valid_tags):
            continue

        variant_id = variant.get("variant_id")
        created_at = variant.get("created_at")
        entry = {
            "variant_id": variant_id,
            "sku": variant.get("sku"),
            "size": variant.get("size"),
            "product_id": variant.get("product_id"),
            "product_title": variant.get("product_title"),
            "created_at_utc": _isoformat_utc(created_at),
            "shopify_available": variant.get("available"),
        }

        mirror_row = mirror_by_variant_id.get(variant_id)

        if mirror_row is None:
            # Assente dal mirror: lag se creata dopo l'ultimo sync (non ha
            # ancora avuto occasione di essere mirrorata), drift altrimenti
            # (il sync ha avuto tempo e non l'ha assorbita).
            if created_at is not None and last_sync_utc is not None and created_at > last_sync_utc:
                lag.append(entry)
            else:
                drift.append(entry)
            continue

        # Presente nel mirror: confronto SOLO sul prezzo, mai sullo stock.
        shopify_price = _to_decimal(variant.get("price"))
        mirror_price = _to_decimal(mirror_row.get("price"))
        if shopify_price is None or mirror_price is None or shopify_price == mirror_price:
            continue

        updated_at = variant.get("updated_at")
        if updated_at is not None and last_sync_utc is not None and updated_at < last_sync_utc:
            drift.append(entry)
        # else: aggiornata DOPO l'ultimo sync -> lag atteso, non si riporta.

    return {"lag": lag, "drift": drift}


def _flatten_products(products: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Appiattisce prodotti normalizzati (Shopify) in una lista di varianti."""
    flat: List[Dict[str, Any]] = []
    for product in products:
        product_id = product.get("id")
        product_title = product.get("title", "")
        product_status = product.get("status", "")
        product_tags = product.get("tags", [])
        for variant in product.get("variants", []):
            flat.append({
                "variant_id": variant.get("id"),
                "sku": variant.get("sku", ""),
                "size": variant.get("title", ""),
                "price": variant.get("price"),
                "created_at": _parse_iso(variant.get("created_at")),
                "updated_at": _parse_iso(variant.get("updated_at")),
                "available": variant.get("available"),
                "product_id": product_id,
                "product_title": product_title,
                "product_status": product_status,
                "product_tags": product_tags,
            })
    return flat


def _inconclusive(base: Dict[str, Any], reason: str) -> Dict[str, Any]:
    """Busta di risposta per un check che non e' potuto arrivare a un verdetto."""
    # INVARIANTE: ok e' vero se e solo se checked e' vero AND lag_count == 0
    # AND drift_count == 0.
    return {
        "ok": False,
        "checked": False,
        "status": "inconclusive",
        **base,
        "last_sync_utc": None,
        "last_sync_source": None,
        "mirror_rows": None,
        "shopify_products_checked": None,
        "shopify_variants_checked": None,
        "lag": [],
        "lag_count": 0,
        "drift": [],
        "drift_count": 0,
        "oversell_count": 0,
        "entries_truncated": False,
        "reason": reason,
    }


def _clamp_window_hours(window_hours: int) -> int:
    return max(MIN_WINDOW_HOURS, min(MAX_WINDOW_HOURS, window_hours))


def skipped_response(window_hours: int, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Busta di risposta per "un sync e' in corso": non e' un errore di per se',
    e' un check che scientemente non si prova a fare mentre il mirror viene
    riscritto — confrontare contro una tabella a metà scrittura produrrebbe
    falsi positivi. Il chiamante (`app.py`) la usa PRIMA di invocare
    `run_lag_check()`.

    `ok: False` (Amendment 2, 2026-09-22): "skipped" e' un non-verdetto, esattamente
    come "inconclusive" — il check non e' arrivato a un confronto. `ok: True`
    qui farebbe leggere GREEN a una regola Scheduler wired su `$.ok == false`
    se il watchdog rispondesse sempre "skipped" per qualsiasi motivo, rendendo
    la proprieta' di sicurezza dipendente da una seconda regola Scheduler
    configurata correttamente altrove — stato esterno che questo repo non puo'
    verificare.
    """
    now = now_utc or datetime.now(timezone.utc)
    base = {
        "checked_at_utc": _isoformat_utc(now),
        "checked_at_rome": now.astimezone(ROME_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": _clamp_window_hours(window_hours),
    }
    # INVARIANTE: ok e' vero se e solo se checked e' vero AND lag_count == 0
    # AND drift_count == 0.
    return {
        "ok": False,
        "checked": False,
        "status": "skipped",
        **base,
        "last_sync_utc": None,
        "last_sync_source": None,
        "mirror_rows": None,
        "shopify_products_checked": None,
        "shopify_variants_checked": None,
        "lag": [],
        "lag_count": 0,
        "drift": [],
        "drift_count": 0,
        "oversell_count": 0,
        "entries_truncated": False,
        "reason": None,
    }


def config_error_response(window_hours: int, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Busta "inconclusive" per il caso in cui `Config.from_env()` non trova le
    env var richieste. `Config.from_env()` chiama `sys.exit(1)` (SystemExit,
    non una `Exception`): il chiamante deve intercettarla esplicitamente PRIMA
    che risalga fino al worker WSGI — non la si lascia mai fare.
    """
    now = now_utc or datetime.now(timezone.utc)
    base = {
        "checked_at_utc": _isoformat_utc(now),
        "checked_at_rome": now.astimezone(ROME_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": _clamp_window_hours(window_hours),
    }
    return _inconclusive(base, reason="config_error")


# --- Orchestratore sottile ---

def run_lag_check(
    config,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    now_utc: Optional[datetime] = None,
    max_pages: int = DEFAULT_MAX_PAGES,
    shopify_client: Optional[ShopifyClient] = None,
    db: Optional[Database] = None,
) -> Dict[str, Any]:
    """
    Orchestratore sottile: costruisce (o riusa, se iniettati) client Shopify e
    Database, esegue il confronto e restituisce la busta di risposta completa
    per `/api/lag-check`. Sola lettura ovunque: nessun metodo scrivente di
    Database viene mai chiamato da questo modulo.

    Args:
        config: Config applicativa (per costruire client/db se non iniettati).
        window_hours: ampiezza finestra `updated_at` su Shopify, clampata
            [1, 168].
        now_utc: istante "ora" iniettabile per i test; default
            `datetime.now(timezone.utc)`.
        max_pages: tetto pagine GraphQL (vedi ShopifyClient).
        shopify_client: client Shopify da riusare (test); default un nuovo
            `ShopifyClient(config)`.
        db: connessione DB da riusare, già connessa (test); default un nuovo
            `Database(config)`, connesso e chiuso qui.

    Returns:
        Dict: busta di risposta JSON-serializzabile. `checked` e' sempre True
        qui (il confronto e' arrivato in fondo): `ok` e' True solo per status
        "clean". Lo status "skipped" non e' gestito qui, e' responsabilità del
        chiamante (app.py), che lo restituisce PRIMA di invocare questo
        orchestratore quando un sync e' in corso.
    """
    now = now_utc or datetime.now(timezone.utc)
    window_hours = _clamp_window_hours(window_hours)
    since_utc = now - timedelta(hours=window_hours)

    base = {
        "checked_at_utc": _isoformat_utc(now),
        "checked_at_rome": now.astimezone(ROME_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "window_hours": window_hours,
    }

    owns_db = db is None
    database = db if db is not None else Database(config)
    client = shopify_client if shopify_client is not None else ShopifyClient(config)

    try:
        if owns_db:
            database.connect()

        try:
            last_sync_utc, last_sync_source = database.get_last_sync_run()
            mirror_rows_count = database.get_mirror_count()
        except Exception as exc:
            log(f"⚠️ lag-check: DB irraggiungibile: {exc}")
            return _inconclusive(base, reason="db_unreachable")

        try:
            products, truncated = client.get_recently_updated_products_graphql(
                since_utc, location_name="Magazzino", max_pages=max_pages
            )
        except Exception as exc:
            log(f"⚠️ lag-check: errore Shopify: {exc}")
            return _inconclusive(base, reason="shopify_error")

        if truncated:
            log("⚠️ lag-check: tetto pagine GraphQL raggiunto, risultato incompleto")
            return _inconclusive(base, reason="page_cap_reached")

        flat_variants = _flatten_products(products)

        try:
            variant_ids = [v["variant_id"] for v in flat_variants if v.get("variant_id") is not None]
            mirror_by_variant_id = database.get_mirror_rows_by_variant_ids(variant_ids)
        except Exception as exc:
            log(f"⚠️ lag-check: DB irraggiungibile (mirror lookup): {exc}")
            return _inconclusive(base, reason="db_unreachable")

        result = classify(flat_variants, mirror_by_variant_id, last_sync_utc)

        # lag_count/drift_count sono i totali VERI (l'allarme), calcolati
        # PRIMA del cap — non vanno mai confusi con la lunghezza degli array
        # riportati (il campione), che il cap tronca sotto.
        lag_count = len(result["lag"])
        drift_count = len(result["drift"])
        total_entries = lag_count + drift_count
        entries_truncated = total_entries > MAX_REPORTED_ENTRIES

        # Amendment 4, 2026-09-22: arricchisci TUTTE le entry lag+drift in
        # un'unica get_real_stock_bulk() (query chunked per sku, non piu' un
        # round-trip per entry) — oversell_count e' quindi un totale VERO,
        # esattamente come lag_count/drift_count: nessun cap lo tocca.
        all_entries = result["lag"] + result["drift"]
        pairs = [(entry["sku"], entry["size"]) for entry in all_entries]
        real_stock_by_pair = database.get_real_stock_bulk(pairs)
        for entry in all_entries:
            real_stock = real_stock_by_pair.get((entry["sku"], entry["size"]))
            entry["real_stock"] = real_stock
            available = entry.get("shopify_available")
            if real_stock is not None and available is not None:
                entry["oversell"] = available > real_stock
            else:
                entry["oversell"] = None

        # MAX_REPORTED_ENTRIES resta SOLO un tetto di SERIALIZZAZIONE su
        # lag+drift combinate, "lag" prima di "drift" (stesso ordine dello
        # status): oltre il cap la entry resta classificata, contata E
        # arricchita, solo non compare nel body. entries_truncated segnala
        # che gli array sono un campione — nessun conteggio ne e' toccato.
        reported_lag = result["lag"][:MAX_REPORTED_ENTRIES]
        reported_drift = result["drift"][: max(0, MAX_REPORTED_ENTRIES - lag_count)]

        oversell_count = sum(1 for e in all_entries if e.get("oversell") is True)

        if lag_count and drift_count:
            status = "lag+drift"
        elif lag_count:
            status = "lag"
        elif drift_count:
            status = "drift"
        else:
            status = "clean"

        checked = True
        # INVARIANTE: ok e' vero se e solo se checked e' vero AND lag_count ==
        # 0 AND drift_count == 0. La troncatura del dettaglio (entries_truncated)
        # non la tocca: il verdetto resta valido anche quando il campione e'
        # parziale.
        ok = checked and lag_count == 0 and drift_count == 0

        return {
            "ok": ok,
            "checked": checked,
            "status": status,
            **base,
            "last_sync_utc": _isoformat_utc(last_sync_utc),
            "last_sync_source": last_sync_source,
            "mirror_rows": mirror_rows_count,
            "shopify_products_checked": len(products),
            "shopify_variants_checked": len(flat_variants),
            "lag": reported_lag,
            "lag_count": lag_count,
            "drift": reported_drift,
            "drift_count": drift_count,
            "oversell_count": oversell_count,
            "entries_truncated": entries_truncated,
            "reason": None,
        }
    except Exception as exc:
        # Qualunque altro errore non previsto (es. un bug in classify() o
        # altrove, non una eccezione DB) — mai far trapelare l'eccezione, mai
        # un traceback nel body. reason "internal_error" e non
        # "db_unreachable": i due handler DB-specific sopra hanno gia'
        # intercettato i casi in cui e' davvero il DB a essere irraggiungibile,
        # e un lettore non va indirizzato al posto sbagliato.
        log(f"⚠️ lag-check: errore inatteso: {exc}")
        return _inconclusive(base, reason="internal_error")
    finally:
        if owns_db:
            try:
                database.close()
            except Exception:
                pass
