"""
Test per src/lag_check.py — classify() puro e run_lag_check() orchestratore —
e per i metodi di sola lettura di src/db.py usati dal lag-check.

Mock di Shopify e MySQL ovunque: nessuna dipendenza esterna, come da stile
test_sync.py / test_app.py / test_db.py.
"""

import json
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.config import Config, VALID_TAGS
from src.db import Database
from src.lag_check import (
    classify,
    config_error_response,
    run_lag_check,
    skipped_response,
)


VALID_TAG = next(iter(VALID_TAGS))
LAST_SYNC = datetime(2026, 9, 22, 1, 4, 0, tzinfo=timezone.utc)


def _make_config() -> Config:
    return Config(
        shop_domain="shop.example.com",
        access_token="tok",
        api_version="2024-04",
        db_host="db.example.com",
        db_user="u",
        db_pass="p",
        db_name="racoon",
    )


def _variant(**overrides):
    base = {
        "variant_id": 1001,
        "sku": "SKU-BASE-DIR",
        "size": "43",
        "price": "49.90",
        "created_at": LAST_SYNC + timedelta(hours=1),
        "updated_at": LAST_SYNC + timedelta(hours=1),
        "available": 2,
        "product_id": 555,
        "product_title": "Sneaker Personalizzata",
        "product_status": "ACTIVE",
        "product_tags": [VALID_TAG],
    }
    base.update(overrides)
    return base


def _mirror_row(**overrides):
    base = {
        "sku": "SKU-BASE-DIR",
        "variant_title": "43",
        "price": Decimal("49.90"),
        "inventory_item_id": 9999,
    }
    base.update(overrides)
    return base


# --- classify() — funzione pura ---

class TestClassify:
    def test_absent_created_after_last_sync_is_lag(self):
        """Caso 1: assente + createdAt dopo l'ultimo sync -> lag."""
        variant = _variant(created_at=LAST_SYNC + timedelta(hours=1))
        result = classify([variant], {}, LAST_SYNC)
        assert result["drift"] == []
        assert len(result["lag"]) == 1
        assert result["lag"][0]["variant_id"] == 1001

    def test_absent_created_before_last_sync_is_drift(self):
        """Caso 2: assente + createdAt prima dell'ultimo sync -> drift."""
        variant = _variant(created_at=LAST_SYNC - timedelta(hours=1))
        result = classify([variant], {}, LAST_SYNC)
        assert result["lag"] == []
        assert len(result["drift"]) == 1
        assert result["drift"][0]["variant_id"] == 1001

    def test_present_same_price_is_clean(self):
        """Caso 3: presente, prezzo uguale -> nessuna entry."""
        variant = _variant(price="49.90")
        mirror = {1001: _mirror_row(price=Decimal("49.90"))}
        result = classify([variant], mirror, LAST_SYNC)
        assert result == {"lag": [], "drift": []}

    def test_present_price_differs_updated_before_last_sync_is_drift(self):
        """Caso 4: presente, prezzo diverso, updatedAt prima dell'ultimo sync -> drift."""
        variant = _variant(price="59.90", updated_at=LAST_SYNC - timedelta(hours=1))
        mirror = {1001: _mirror_row(price=Decimal("49.90"))}
        result = classify([variant], mirror, LAST_SYNC)
        assert result["lag"] == []
        assert len(result["drift"]) == 1

    def test_present_price_differs_updated_after_last_sync_not_reported(self):
        """Caso 5: presente, prezzo diverso, updatedAt DOPO l'ultimo sync -> lag atteso, non riportato."""
        variant = _variant(price="59.90", updated_at=LAST_SYNC + timedelta(hours=1))
        mirror = {1001: _mirror_row(price=Decimal("49.90"))}
        result = classify([variant], mirror, LAST_SYNC)
        assert result == {"lag": [], "drift": []}

    def test_missing_every_valid_tag_is_ignored(self):
        """Caso 6: prodotto senza nessun tag di VALID_TAGS -> ignorato."""
        variant = _variant(product_tags=["accessori", "borse"], created_at=LAST_SYNC + timedelta(hours=1))
        result = classify([variant], {}, LAST_SYNC)
        assert result == {"lag": [], "drift": []}

    @pytest.mark.parametrize("status", ["DRAFT", "ARCHIVED", "draft", "archived"])
    def test_non_active_product_is_ignored(self, status):
        """Caso 7: prodotto DRAFT/ARCHIVED -> ignorato (case-insensitive)."""
        variant = _variant(product_status=status, created_at=LAST_SYNC + timedelta(hours=1))
        result = classify([variant], {}, LAST_SYNC)
        assert result == {"lag": [], "drift": []}

    @pytest.mark.parametrize("title", ["Sneaker Outlet", "SNEAKER OUTLET", "sneaker outlet edition"])
    def test_outlet_product_title_is_ignored(self, title):
        """Caso 8: Product_Title outlet -> ignorato, mirror del filtro `NOT LIKE '%utlet%'` della SP."""
        variant = _variant(product_title=title, created_at=LAST_SYNC + timedelta(hours=1))
        result = classify([variant], {}, LAST_SYNC)
        assert result == {"lag": [], "drift": []}

    def test_present_price_unparsable_is_ignored_not_raised(self):
        """Un prezzo non numerico non deve far esplodere classify(): si ignora la entry."""
        variant = _variant(price="not-a-number")
        mirror = {1001: _mirror_row(price=Decimal("49.90"))}
        result = classify([variant], mirror, LAST_SYNC)
        assert result == {"lag": [], "drift": []}


# --- run_lag_check() — orchestratore, con Shopify/DB finti ---

class FakeShopifyClient:
    def __init__(self, products=None, truncated=False, raise_exc=None):
        self.products = products or []
        self.truncated = truncated
        self.raise_exc = raise_exc
        self.calls = []

    def get_recently_updated_products_graphql(self, since_utc, location_name="Magazzino", max_pages=20):
        self.calls.append((since_utc, location_name, max_pages))
        if self.raise_exc:
            raise self.raise_exc
        return self.products, self.truncated


class FakeDB:
    """
    `real_stock_map` mima il contratto di `get_real_stock_bulk` (Amendment 4):
    una coppia ASSENTE dalla mappa e' 0 (nessuna riga, un fatto — default
    volutamente pericoloso da sbagliare, vedi contratto in `src/db.py`), una
    coppia mappata esplicitamente a `None` rappresenta un chunk fallito per
    QUELLA coppia (gestito con garbo, non solleva). `raise_on_real_stock`
    resta un'altra cosa: fa sollevare l'intera chiamata (non gestito da
    nessun try/except DB-specific in `run_lag_check`), per il test che prova
    il catch-all — la Database reale non si comporta cosi' (il suo
    get_real_stock_bulk non solleva mai), e' un doppio deliberatamente
    "cattivo" per quel solo scenario.
    """

    def __init__(
        self,
        last_sync=(LAST_SYNC, "scheduler_job_logs"),
        mirror_count=100,
        mirror_rows=None,
        real_stock_map=None,
        raise_on_last_sync=None,
        raise_on_mirror_count=None,
        raise_on_mirror_rows=None,
        raise_on_real_stock=None,
    ):
        self.last_sync = last_sync
        self.mirror_count = mirror_count
        self.mirror_rows = mirror_rows or {}
        self.real_stock_map = real_stock_map or {}
        self.raise_on_last_sync = raise_on_last_sync
        self.raise_on_mirror_count = raise_on_mirror_count
        self.raise_on_mirror_rows = raise_on_mirror_rows
        self.raise_on_real_stock = raise_on_real_stock
        self.real_stock_calls = []
        self.real_stock_bulk_calls = []
        self.connected = False
        self.closed = False

    def connect(self):
        self.connected = True
        return self

    def close(self):
        self.closed = True

    def get_last_sync_run(self):
        if self.raise_on_last_sync:
            raise self.raise_on_last_sync
        return self.last_sync

    def get_mirror_count(self):
        if self.raise_on_mirror_count:
            raise self.raise_on_mirror_count
        return self.mirror_count

    def get_mirror_rows_by_variant_ids(self, ids):
        if self.raise_on_mirror_rows:
            raise self.raise_on_mirror_rows
        ids = set(ids)
        return {vid: row for vid, row in self.mirror_rows.items() if vid in ids}

    def get_real_stock(self, sku, size):
        """Non piu' chiamato da run_lag_check (Amendment 4) — resta solo per
        completezza dell'interfaccia e per i test che asserisco NON venga
        invocato (niente N+1)."""
        self.real_stock_calls.append((sku, size))
        if self.raise_on_real_stock:
            raise self.raise_on_real_stock
        return self.real_stock_map.get((sku, size))

    def get_real_stock_bulk(self, pairs):
        pairs = list(pairs)
        self.real_stock_bulk_calls.append(pairs)
        if self.raise_on_real_stock:
            raise self.raise_on_real_stock
        return {pair: self.real_stock_map.get(pair, 0) for pair in pairs}


def _product(
    variant_id=1001,
    sku="SKU-BASE-DIR",
    size="43",
    created_at=None,
    updated_at=None,
    price="49.90",
    available=2,
    tags=None,
    status="ACTIVE",
    title="Sneaker Personalizzata",
):
    created_at = created_at or (LAST_SYNC + timedelta(hours=1))
    updated_at = updated_at or created_at
    return {
        "id": 555,
        "title": title,
        "status": status,
        "tags": tags if tags is not None else [VALID_TAG],
        "variants": [{
            "id": variant_id,
            "title": size,
            "sku": sku,
            "price": price,
            "created_at": created_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_at": updated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "available": available,
        }],
    }


class TestRunLagCheck:
    def test_clean_when_nothing_to_report(self):
        db = FakeDB()
        client = FakeShopifyClient(products=[])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "clean"
        assert result["ok"] is True
        assert result["checked"] is True
        assert result["reason"] is None

    def test_db_unreachable_is_inconclusive_and_hides_exception_text(self):
        """Caso 10: DB irraggiungibile -> inconclusive, ok:false, niente testo dell'eccezione nel body."""
        db = FakeDB(raise_on_last_sync=Exception("ultra-secret-db-trace-12345"))
        client = FakeShopifyClient(products=[])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "inconclusive"
        assert result["ok"] is False
        assert result["checked"] is False
        assert result["reason"] == "db_unreachable"
        body_text = json.dumps(result)
        assert "ultra-secret-db-trace-12345" not in body_text

    def test_mirror_lookup_failure_is_also_db_unreachable(self):
        db = FakeDB(raise_on_mirror_rows=Exception("connection reset"))
        client = FakeShopifyClient(products=[_product()])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "inconclusive"
        assert result["ok"] is False
        assert result["checked"] is False
        assert result["reason"] == "db_unreachable"

    def test_shopify_error_is_inconclusive(self):
        """Caso 11: Shopify solleva -> inconclusive, ok:false, checked:false."""
        db = FakeDB()
        client = FakeShopifyClient(raise_exc=Exception("shopify is down"))
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "inconclusive"
        assert result["ok"] is False
        assert result["checked"] is False
        assert result["reason"] == "shopify_error"

    def test_page_cap_reached_is_inconclusive(self):
        """Caso 12: tetto pagine raggiunto -> inconclusive, ok:false, checked:false, reason page_cap_reached."""
        db = FakeDB()
        client = FakeShopifyClient(products=[_product()], truncated=True)
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "inconclusive"
        assert result["ok"] is False
        assert result["checked"] is False
        assert result["reason"] == "page_cap_reached"
        # Un risultato incompleto non deve MAI arrivare travestito da pulito.
        assert result["lag"] == []
        assert result["drift"] == []

    def test_unexpected_non_db_error_yields_internal_error(self):
        """Fix collaterale: un'eccezione non-DB (es. un bug in classify() o
        nell'arricchimento) NON deve finire sotto reason 'db_unreachable' —
        indirizzerebbe un lettore al posto sbagliato. get_real_stock() e'
        fuori dai try/except DB-specific: farla sollevare colpisce solo il
        catch-all."""
        db = FakeDB(raise_on_real_stock=Exception("bug altrove, non nel DB"))
        client = FakeShopifyClient(products=[_product(created_at=LAST_SYNC + timedelta(hours=1))])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "inconclusive"
        assert result["ok"] is False
        assert result["checked"] is False
        assert result["reason"] == "internal_error"

    def test_get_real_stock_bulk_pair_failure_keeps_entry_with_null_stock_and_oversell(self):
        """Caso 14 (Amendment 4): la coppia e' esplicitamente None nella mappa
        (il suo chunk e' fallito, gestito con garbo da get_real_stock_bulk,
        senza sollevare) -> real_stock/oversell null, entry comunque
        riportata. NON piu' simulato con una mappa vuota: dall'amendment una
        coppia ASSENTE dalla mappa e' 0 (nessuna riga, un fatto), non None."""
        db = FakeDB(real_stock_map={("SKU-BASE-DIR", "43"): None})
        client = FakeShopifyClient(products=[_product()])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag_count"] == 1
        entry = result["lag"][0]
        assert entry["real_stock"] is None
        assert entry["oversell"] is None
        # Amendment 1: checked:true e ok:false devono poter convivere -- questa
        # coppia e' quello che prova che i due flag sono indipendenti.
        assert result["checked"] is True
        assert result["ok"] is False

    def test_oversell_true_when_available_exceeds_real_stock(self):
        """Caso 15: available 2 vs real_stock 0 -> oversell true, oversell_count 1."""
        db = FakeDB(real_stock_map={("SKU-BASE-DIR", "43"): 0})
        client = FakeShopifyClient(products=[_product(available=2)])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["oversell_count"] == 1
        assert result["lag"][0]["oversell"] is True
        assert result["lag"][0]["real_stock"] == 0

    def test_oversell_true_when_pair_absent_from_real_stock_map_defaults_to_zero(self):
        """Amendment 4, test 3 (direzione pericolosa): la coppia NON compare
        nella mappa (nessuna riga di stock, non un fallimento) -> 0, non None
        — available 2 > 0 produce oversell True. E' la direzione che
        l'amendment non puo' sbagliare: un oversell vero non deve leggere
        come sconosciuto."""
        db = FakeDB(real_stock_map={})
        client = FakeShopifyClient(products=[_product(available=2)])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag"][0]["real_stock"] == 0
        assert result["lag"][0]["oversell"] is True
        assert result["oversell_count"] == 1

    def test_real_stock_int_survives_json_serialisation(self):
        """Caso 13 (end-to-end): real_stock intero arriva pulito fino al body serializzabile."""
        db = FakeDB(real_stock_map={("SKU-BASE-DIR", "43"): 9})
        client = FakeShopifyClient(products=[_product()])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        entry = result["lag"][0]
        assert entry["real_stock"] == 9
        assert isinstance(entry["real_stock"], int)
        json.dumps(result)  # non solleva: niente Decimal residuo

    def test_last_sync_source_assumed_propagates_to_response(self):
        """Caso 17 (lato orchestratore): sorgente 'assumed_schedule' arriva fino al body, check completo."""
        db = FakeDB(last_sync=(LAST_SYNC, "assumed_schedule"))
        client = FakeShopifyClient(products=[])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["last_sync_source"] == "assumed_schedule"
        assert result["status"] == "clean"
        assert result["ok"] is True

    def test_status_is_lag_plus_drift_when_both_present(self):
        p_lag = _product(variant_id=1001, sku="SKU-A", created_at=LAST_SYNC + timedelta(hours=1))
        p_drift = _product(variant_id=1002, sku="SKU-B", created_at=LAST_SYNC - timedelta(hours=1))
        db = FakeDB()
        client = FakeShopifyClient(products=[p_lag, p_drift])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "lag+drift"
        assert result["lag_count"] == 1
        assert result["drift_count"] == 1
        assert result["ok"] is False

    def test_excluded_variants_do_not_appear_in_lag_or_drift(self):
        """Outlet/DRAFT/senza tag: contati in shopify_variants_checked ma mai riportati."""
        excluded = _product(variant_id=2001, title="Sneaker Outlet", created_at=LAST_SYNC + timedelta(hours=1))
        db = FakeDB()
        client = FakeShopifyClient(products=[excluded])
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["status"] == "clean"
        assert result["shopify_variants_checked"] == 1
        assert result["lag_count"] == 0

    @pytest.mark.parametrize("window_hours,expected", [(99999, 168), (0, 1), (48, 48)])
    def test_window_hours_is_clamped(self, window_hours, expected):
        db = FakeDB()
        client = FakeShopifyClient(products=[])
        result = run_lag_check(_make_config(), db=db, shopify_client=client, window_hours=window_hours)
        assert result["window_hours"] == expected


def _bulk_lag_products(n, sku_prefix="SKU-BULK"):
    """n prodotti distinti, ciascuno con una variante assente dal mirror e
    creata dopo LAST_SYNC -> n entry 'lag' indipendenti."""
    return [
        _product(variant_id=9000 + i, sku=f"{sku_prefix}-{i}", created_at=LAST_SYNC + timedelta(hours=1))
        for i in range(n)
    ]


class TestReportedEntriesCap:
    """Amendment 3: MAX_REPORTED_ENTRIES limita l'arricchimento/serializzazione,
    mai i conteggi veri."""

    def test_over_cap_truncates_array_but_keeps_true_count(self):
        db = FakeDB()
        client = FakeShopifyClient(products=_bulk_lag_products(60))
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag_count"] == 60
        assert len(result["lag"]) <= 50
        assert result["entries_truncated"] is True
        assert result["checked"] is True
        assert result["ok"] is False

    def test_exactly_cap_entries_not_truncated(self):
        db = FakeDB()
        client = FakeShopifyClient(products=_bulk_lag_products(50))
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag_count"] == 50
        assert len(result["lag"]) == 50
        assert result["entries_truncated"] is False

    def test_oversell_count_is_true_total_under_truncation(self):
        """Amendment 4, test 5: oversell_count resta un totale VERO anche
        quando gli array lag/drift sono troncati a 50 — 60 entry, 55 in
        oversell (default 'nessuna riga -> 0' per 55 sku, i 5 rimanenti hanno
        stock sufficiente a non essere oversell)."""
        n = 60
        not_oversell_from = 55
        products = _bulk_lag_products(n)
        real_stock_map = {
            (f"SKU-BULK-{i}", "43"): 100 for i in range(not_oversell_from, n)
        }
        db = FakeDB(real_stock_map=real_stock_map)
        client = FakeShopifyClient(products=products)
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag_count"] == n
        assert result["oversell_count"] == not_oversell_from
        assert len(result["lag"]) + len(result["drift"]) <= 50
        assert result["entries_truncated"] is True

    def test_enrichment_is_not_capped_only_serialisation_is(self):
        """Amendment 4: il cap NON limita piu' l'arricchimento — tutte le 60
        entry ricevono real_stock/oversell, solo gli array lag/drift
        restano tagliati a MAX_REPORTED_ENTRIES."""
        db = FakeDB()
        client = FakeShopifyClient(products=_bulk_lag_products(60))
        result = run_lag_check(_make_config(), db=db, shopify_client=client)
        assert result["lag_count"] == 60
        assert len(result["lag"]) <= 50
        # Le entry FUORI dal campione serializzato non esistono nel body, ma
        # il conteggio oversell a valle (vedi TestReportedEntriesCap) prova
        # che sono state comunque arricchite.
        assert all("real_stock" in e for e in result["lag"])

    def test_no_n_plus_1_get_real_stock_bulk_called_once_not_per_entry(self):
        """Amendment 4, test 6: niente N+1 — get_real_stock_bulk() e' chiamato
        al massimo ceil(sku distinti / 500) volte (qui: 1, sotto lo stesso
        chunk lato run_lag_check), MAI una volta per entry; get_real_stock()
        (per-entry) non viene mai invocato. Asserzione sul CONTEGGIO delle
        chiamate al fake, non sull'output."""
        db = FakeDB()
        client = FakeShopifyClient(products=_bulk_lag_products(60))
        run_lag_check(_make_config(), db=db, shopify_client=client)
        distinct_skus = 60  # _bulk_lag_products usa uno sku distinto per prodotto
        assert len(db.real_stock_bulk_calls) <= math.ceil(distinct_skus / 500)
        assert len(db.real_stock_calls) == 0
        assert len(db.real_stock_calls) != 60


def _clean_result():
    db = FakeDB()
    client = FakeShopifyClient(products=[])
    return run_lag_check(_make_config(), db=db, shopify_client=client)


def _lag_result():
    db = FakeDB()
    client = FakeShopifyClient(products=[_product(created_at=LAST_SYNC + timedelta(hours=1))])
    return run_lag_check(_make_config(), db=db, shopify_client=client)


def _drift_result():
    db = FakeDB()
    client = FakeShopifyClient(products=[_product(created_at=LAST_SYNC - timedelta(hours=1))])
    return run_lag_check(_make_config(), db=db, shopify_client=client)


def _lag_plus_drift_result():
    p_lag = _product(variant_id=1001, sku="SKU-A", created_at=LAST_SYNC + timedelta(hours=1))
    p_drift = _product(variant_id=1002, sku="SKU-B", created_at=LAST_SYNC - timedelta(hours=1))
    db = FakeDB()
    client = FakeShopifyClient(products=[p_lag, p_drift])
    return run_lag_check(_make_config(), db=db, shopify_client=client)


def _inconclusive_result():
    db = FakeDB(raise_on_last_sync=Exception("down"))
    client = FakeShopifyClient(products=[])
    return run_lag_check(_make_config(), db=db, shopify_client=client)


def _skipped_result():
    return skipped_response(48)


class TestOkCheckedInvariant:
    """Amendment 2, test 4: `ok` e' vero se e solo se `checked` e' vero AND
    `lag_count == 0` AND `drift_count == 0` — su OGNI status esistente,
    table-driven apposta: un domani che aggiunga un settimo status senza
    rispettare l'invariante fa fallire QUESTO test, non uno status-specifico
    che nessuno penserebbe di aggiornare."""

    @pytest.mark.parametrize(
        "status, make_result",
        [
            ("clean", _clean_result),
            ("lag", _lag_result),
            ("drift", _drift_result),
            ("lag+drift", _lag_plus_drift_result),
            ("inconclusive", _inconclusive_result),
            ("skipped", _skipped_result),
        ],
    )
    def test_invariant_holds(self, status, make_result):
        result = make_result()
        assert result["status"] == status
        assert result["ok"] == (
            result["checked"] and result["lag_count"] == 0 and result["drift_count"] == 0
        )


class TestEnvelopeHelpers:
    def test_skipped_response_shape(self):
        """Amendment 2, test 3: skipped -> ok:False, checked:False. Prima di
        Amendment 2 'skipped' rispondeva ok:True; un watchdog che risponde
        sempre 'skipped' leggerebbe GREEN a una regola Scheduler su
        `$.ok == false` — skipped e' un non-verdetto come inconclusive."""
        resp = skipped_response(48)
        assert resp["status"] == "skipped"
        assert resp["ok"] is False
        assert resp["checked"] is False
        assert resp["lag"] == [] and resp["drift"] == []
        assert resp["reason"] is None
        json.dumps(resp)

    def test_config_error_response_is_inconclusive(self):
        resp = config_error_response(48)
        assert resp["status"] == "inconclusive"
        assert resp["ok"] is False
        assert resp["checked"] is False
        assert resp["reason"] == "config_error"
        json.dumps(resp)


# --- Database — metodi di sola lettura del lag-check (cursore iniettato a mano) ---

class TestDatabaseLagCheckMethods:
    """
    Stesso principio di test_db.py (nessuna rete reale): qui il cursore/la
    connessione sono mockati direttamente sull'istanza, senza passare da
    connect()/build_ssl_config, perché questi metodi non toccano la logica TLS.
    """

    def _db_with_cursor(self, cursor):
        db = Database(_make_config())
        db._cursor = cursor
        db._connection = MagicMock()
        return db

    def test_get_mirror_count(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = (9195,)
        db = self._db_with_cursor(cursor)
        assert db.get_mirror_count() == 9195

    def test_get_mirror_rows_by_variant_ids_chunks_and_maps_by_id(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [
            (1001, "SKU-BASE-DIR", "43", Decimal("49.90"), 555),
        ]
        db = self._db_with_cursor(cursor)
        result = db.get_mirror_rows_by_variant_ids([1001])
        assert result == {
            1001: {
                "sku": "SKU-BASE-DIR",
                "variant_title": "43",
                "price": Decimal("49.90"),
                "inventory_item_id": 555,
            }
        }

    def test_get_mirror_rows_by_variant_ids_empty_input_no_query(self):
        cursor = MagicMock()
        db = self._db_with_cursor(cursor)
        result = db.get_mirror_rows_by_variant_ids([])
        assert result == {}
        cursor.execute.assert_not_called()

    def test_get_real_stock_casts_decimal_to_int(self):
        """Caso 13 (lato DB): SUM() torna Decimal, get_real_stock (wrapper su
        get_real_stock_bulk) casta a int."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [("SKU-BASE-DIR", "43", Decimal("9"))]
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock("SKU-BASE-DIR", "43")
        assert result == 9
        assert isinstance(result, int)

    def test_get_real_stock_query_shape_and_params(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        db = self._db_with_cursor(cursor)
        db.get_real_stock("SKU-BASE-DIR", "43")
        args, _ = cursor.execute.call_args
        query, params = args[0], args[1]
        assert params == ("SKU-BASE-DIR", "43")
        assert "IFNULL(SUM(s.QTY), 0)" in query
        assert "sku_root" in query
        assert "RAW_SHOE_SKU" in query
        assert "GROUP BY" in query

    def test_get_real_stock_returns_none_on_failure_not_zero_not_raise(self):
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("grant denied")
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock("SKU-BASE-DIR", "43")
        assert result is None

    def test_get_real_stock_bulk_pair_absent_from_result_set_is_zero(self):
        """Amendment 4, test 1: nessuna riga affatto per la coppia (chunk
        riuscito, GROUP BY non produce righe per lei) -> 0, non None. E' la
        riga pericolosa del contratto: assenza = fatto, non sconosciuto."""
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock_bulk([("SKU-X", "40")])
        assert result == {("SKU-X", "40"): 0}

    def test_get_real_stock_bulk_pair_summing_to_zero_is_zero(self):
        """Amendment 4, test 2: riga presente ma SUM(QTY) e' 0 -> stesso 0,
        percorso diverso (riga con qty=0 invece di nessuna riga), stesso
        numero — entrambi devono atterrare li'."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [("SKU-X", "40", Decimal("0"))]
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock_bulk([("SKU-X", "40")])
        assert result == {("SKU-X", "40"): 0}

    def test_get_real_stock_bulk_casts_decimal_qty_to_int(self):
        """Amendment 4, test 7: Decimal('9') dal driver -> int 9 serializzabile."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [("SKU-X", "40", Decimal("9"))]
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock_bulk([("SKU-X", "40")])
        assert result[("SKU-X", "40")] == 9
        assert isinstance(result[("SKU-X", "40")], int)
        json.dumps(result[("SKU-X", "40")])

    def test_get_real_stock_bulk_chunk_isolation_first_succeeds_second_raises(self):
        """Amendment 4, test 4: primo chunk riesce, secondo solleva -> le
        coppie del primo chunk sono numeri (anche quella senza riga: 0, un
        fatto), OGNI coppia del secondo chunk e' None (non 0) — la fixture
        che prova che la seminatura degli 0 avviene PER CHUNK, dopo il suo
        successo, mai globalmente prima del loop."""
        cursor = MagicMock()
        cursor.fetchall.return_value = [("SKU-A", "40", Decimal("3"))]
        cursor.execute.side_effect = [None, Exception("chunk 2 irraggiungibile")]
        db = self._db_with_cursor(cursor)
        db.STOCK_CHUNK_SIZE = 2  # forza 2 chunk con solo 4 sku distinti
        pairs = [("SKU-A", "40"), ("SKU-B", "41"), ("SKU-C", "42"), ("SKU-D", "43")]
        result = db.get_real_stock_bulk(pairs)
        assert result[("SKU-A", "40")] == 3
        assert result[("SKU-B", "41")] == 0
        assert result[("SKU-C", "42")] is None
        assert result[("SKU-D", "43")] is None

    def test_get_real_stock_bulk_empty_input_no_query(self):
        cursor = MagicMock()
        db = self._db_with_cursor(cursor)
        result = db.get_real_stock_bulk([])
        assert result == {}
        cursor.execute.assert_not_called()

    def test_get_last_sync_run_uses_observed_log_when_available(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = ("2026-09-22 01:04:00",)
        db = self._db_with_cursor(cursor)
        dt, source = db.get_last_sync_run()
        assert source == "scheduler_job_logs"
        assert dt == datetime(2026, 9, 22, 1, 4, 0, tzinfo=timezone.utc)

    def test_get_last_sync_run_falls_back_to_assumed_schedule_on_failure(self):
        """Caso 17 (lato DB): scheduler_job_logs irraggiungibile -> assumed_schedule, mai un raise."""
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("scheduler_job_logs unreachable")
        db = self._db_with_cursor(cursor)
        dt, source = db.get_last_sync_run()
        assert source == "assumed_schedule"
        assert dt is not None
        assert dt.tzinfo is not None

    def test_get_last_sync_run_falls_back_when_no_row(self):
        cursor = MagicMock()
        cursor.fetchone.return_value = None
        db = self._db_with_cursor(cursor)
        dt, source = db.get_last_sync_run()
        assert source == "assumed_schedule"
        assert dt is not None
