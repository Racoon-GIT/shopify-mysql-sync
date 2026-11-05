"""
Test per il passo GraphQL "collezioni nuovo modello" (API 2026-10) in
ShopifyClient.build_product_collections_map e per il suo cablaggio
(shopify_to_mysql.sync_products_graphql, app.run_sync, /api/status).

Solo mock: una sessione HTTP finta al posto di requests.Session. Nessuna
chiamata a Shopify, nessun DB. Le attese sono letterali (mai derivate dal codice
sotto test); gli id delle collezioni sono scelti perche' ordine numerico e ordine
di stringa divergano (77690241136 < 608803651916 ma "608..." < "776...").
"""

import importlib
import os
from unittest.mock import MagicMock

import pytest
import requests

import shopify_to_mysql
from src import shopify_client as shopify_client_module
from src.config import Config, NEW_MODEL_COLLECTIONS_API_VERSION
from src.shopify_client import ShopifyClient


SHOP = "shop.example.com"
REST_BASE = f"https://{SHOP}/admin/api/2024-04"
GQL_PIN_URL = f"{REST_BASE}/graphql.json"
GQL_NEW_URL = f"https://{SHOP}/admin/api/2026-10/graphql.json"


def _make_config() -> Config:
    return Config(
        shop_domain=SHOP,
        access_token="tok",
        api_version="2024-04",
        db_host="db.example.com",
        db_user="u",
        db_pass="p",
        db_name="racoon",
    )


# --- Doppi: risposta e sessione -------------------------------------------------

class FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.text = str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code} error")


class FakeSession:
    """Sostituisce requests.Session: registra ogni chiamata REST e GraphQL."""

    def __init__(self, rest, graphql_responder):
        self.rest = rest
        self.graphql_responder = graphql_responder
        self.rest_calls = []      # [(method, url)]
        self.graphql_calls = []   # [(url, payload)]

    def request(self, method, url, json=None, params=None):
        self.rest_calls.append((method, url))
        return self.rest[url]()

    def post(self, url, json=None):
        self.graphql_calls.append((url, json))
        return self.graphql_responder(url, json)


def _rest(body, link=None):
    headers = {"Link": f'<{link}>; rel="next"'} if link else {}
    return lambda: FakeResponse(body, 200, headers)


# REST: custom 500 "Custom A" (pagina 1), 501 "Custom, B" (pagina 2), smart 700 "Smart più".
REST_FIXTURE = {
    f"{REST_BASE}/custom_collections.json?limit=250": _rest(
        {"custom_collections": [{"id": 500, "title": "Custom A"}]},
        link=f"{REST_BASE}/custom_collections.json?limit=250&page_info=p2",
    ),
    f"{REST_BASE}/custom_collections.json?limit=250&page_info=p2": _rest(
        {"custom_collections": [{"id": 501, "title": "Custom, B"}]}
    ),
    f"{REST_BASE}/smart_collections.json?limit=250": _rest(
        {"smart_collections": [{"id": 700, "title": "Smart più"}]}
    ),
    f"{REST_BASE}/collections/500/products.json?limit=250": _rest(
        {"products": [{"id": 11}, {"id": 12}]}
    ),
    f"{REST_BASE}/collections/501/products.json?limit=250": _rest(
        {"products": [{"id": 12}]}
    ),
    f"{REST_BASE}/collections/700/products.json?limit=250": _rest(
        {"products": [{"id": 11}, {"id": 13}]}
    ),
}

REST_CALLS_EXPECTED = [
    ("GET", f"{REST_BASE}/custom_collections.json?limit=250"),
    ("GET", f"{REST_BASE}/collections/500/products.json?limit=250"),
    ("GET", f"{REST_BASE}/custom_collections.json?limit=250&page_info=p2"),
    ("GET", f"{REST_BASE}/collections/501/products.json?limit=250"),
    ("GET", f"{REST_BASE}/smart_collections.json?limit=250"),
    ("GET", f"{REST_BASE}/collections/700/products.json?limit=250"),
]

# Mappa legacy attesa (letterale).
LEGACY = {
    11: ["Custom A", "Smart più"],
    12: ["Custom A", "Custom, B"],
    13: ["Smart più"],
}

NUOVA = "NUOVA COLLEZIONE 2026"
DENIM = "Scarpe in Jeans Denim"
TENDENZA = "Scarpe personalizzate più di tendenza, e alla moda"
BIRK = "Birkenstock Denim"


def _gid(kind, n):
    return f"gid://shopify/{kind}/{n}"


# Listing GraphQL in ordine NON crescente, con gli id REST mescolati ai nuovi.
LISTING = [
    (_gid("Collection", 626360615244), BIRK),
    (_gid("Collection", 500), "Custom A"),
    (_gid("Collection", 77690241136), NUOVA),
    (_gid("Collection", 700), "Smart più"),
    (_gid("Collection", 609674920268), TENDENZA),
    (_gid("Collection", 501), "Custom, B"),
    (_gid("Collection", 608803651916), DENIM),
]

MEMBERS = {
    _gid("Collection", 77690241136): [11, 14, 16],
    _gid("Collection", 608803651916): [11, 12, 15],
    _gid("Collection", 609674920268): [15],
    _gid("Collection", 626360615244): [],
    # Membri di collezioni REST: non devono mai essere richiesti.
    _gid("Collection", 500): [90],
    _gid("Collection", 501): [91],
    _gid("Collection", 700): [92],
}

# Mappa unita attesa (letterale): legacy + titoli accodati, id collezione crescente.
MERGED = {
    11: ["Custom A", "Smart più", NUOVA, DENIM],
    12: ["Custom A", "Custom, B", DENIM],
    13: ["Smart più"],
    14: [NUOVA],
    15: [DENIM, TENDENZA],
    16: [NUOVA],
}
STATUS_MERGED = "ok: 4 collections, 5 products"


class FakeShop:
    """Server GraphQL finto con paginazione a cursore `c<offset>`."""

    def __init__(self, listing, members, listing_page=250, products_page=250):
        self.listing = listing
        self.members = members
        self.listing_page = listing_page
        self.products_page = products_page

    @staticmethod
    def _page(items, size, cursor):
        offset = 0 if cursor is None else int(cursor[1:])
        chunk = items[offset:offset + size]
        end = offset + size
        return chunk, {
            "hasNextPage": end < len(items),
            "endCursor": f"c{end}" if end < len(items) else None,
        }

    def respond(self, url, payload):
        variables = payload.get("variables") or {}
        cursor = variables.get("cursor")
        if "collection(id" in payload["query"]:
            members = self.members.get(variables["id"])
            if members is None:
                return FakeResponse({"data": {"collection": None}})
            chunk, info = self._page(members, self.products_page, cursor)
            return FakeResponse({"data": {"collection": {"products": {
                "pageInfo": info,
                "nodes": [{"id": _gid("Product", p)} for p in chunk],
            }}}})
        chunk, info = self._page(self.listing, self.listing_page, cursor)
        return FakeResponse({"data": {"collections": {
            "pageInfo": info,
            "nodes": [{"id": g, "title": t} for g, t in chunk],
        }}})


def _is_listing(payload):
    return "collection(id" not in payload["query"]


def _make_client(shop=None, responder=None, rest=None):
    shop = shop or FakeShop(LISTING, MEMBERS)
    client = ShopifyClient(_make_config())
    client._session = FakeSession(rest or REST_FIXTURE, responder or shop.respond)
    return client


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Il retry di graphql() dorme davvero: mai nei test."""
    monkeypatch.setattr("time.sleep", lambda s: None)


@pytest.fixture(autouse=True)
def _clean_status():
    shopify_client_module.reset_new_model_collections_status()
    yield
    shopify_client_module.reset_new_model_collections_status()


def _log_lines(capsys):
    return capsys.readouterr().out.splitlines()


# --- Merge, ordine, diff degli id ---------------------------------------------

class TestMerge:
    def test_merged_map_is_legacy_plus_appended_titles(self):
        client = _make_client()
        result = client.build_product_collections_map()
        assert result == MERGED
        assert shopify_client_module.get_new_model_collections_status() == STATUS_MERGED

    def test_numeric_id_order_not_string_order(self):
        # Prodotto 11: NUOVA (77690241136) deve precedere DENIM (608803651916).
        # Ordinando per stringa sarebbe DENIM prima: l'asimmetria qui e' voluta.
        result = _make_client().build_product_collections_map()
        assert result[11][2:] == [NUOVA, DENIM]
        assert result[15] == [DENIM, TENDENZA]  # 608... prima di 609...

    def test_legacy_titles_stay_first_and_in_legacy_order(self):
        result = _make_client().build_product_collections_map()
        assert result[11][:2] == ["Custom A", "Smart più"]
        assert result[12][:2] == ["Custom A", "Custom, B"]

    def test_product_only_in_new_model_gets_its_own_key(self):
        result = _make_client().build_product_collections_map()
        assert result[14] == [NUOVA]
        assert result[16] == [NUOVA]
        assert 13 in result and result[13] == ["Smart più"]  # solo legacy: intatto

    def test_every_legacy_product_survives_the_merge(self):
        result = _make_client().build_product_collections_map()
        assert set(LEGACY) <= set(result)
        assert set(result) == {11, 12, 13, 14, 15, 16}

    def test_titles_with_comma_and_accent_pass_through_byte_exact(self):
        result = _make_client().build_product_collections_map()
        assert result[15][1] == "Scarpe personalizzate più di tendenza, e alla moda"
        assert result[15][1].encode("utf-8") == (
            b"Scarpe personalizzate pi\xc3\xb9 di tendenza, e alla moda"
        )
        assert result[12][1] == "Custom, B"

    def test_rest_ids_excluded_including_second_rest_page(self):
        # 500 (REST p.1), 501 (REST p.2), 700 (smart): mai richiesti come prodotti.
        client = _make_client()
        client.build_product_collections_map()
        asked = [
            payload["variables"]["id"]
            for _, payload in client._session.graphql_calls
            if not _is_listing(payload)
        ]
        assert asked == [
            "gid://shopify/Collection/77690241136",
            "gid://shopify/Collection/608803651916",
            "gid://shopify/Collection/609674920268",
            "gid://shopify/Collection/626360615244",
        ]

    def test_near_miss_ids_are_not_excluded(self):
        # 5000 e 70 contengono/sono contenuti in 500 e 700: sono collezioni diverse.
        listing = [
            (_gid("Collection", 5000), "Cinquemila"),
            (_gid("Collection", 500), "Custom A"),
            (_gid("Collection", 70), "Settanta"),
        ]
        members = {
            _gid("Collection", 5000): [21],
            _gid("Collection", 70): [22],
        }
        result = _make_client(FakeShop(listing, members)).build_product_collections_map()
        assert result == {**LEGACY, 21: ["Cinquemila"], 22: ["Settanta"]}
        assert shopify_client_module.get_new_model_collections_status() == "ok: 2 collections, 2 products"

    def test_status_counts_distinct_products_not_memberships(self):
        # 4 collezioni, 5 prodotti distinti, 7 appartenenze (3+3+1+0).
        _make_client().build_product_collections_map()
        assert shopify_client_module.get_new_model_collections_status() == "ok: 4 collections, 5 products"


class TestNothingFound:
    def test_listing_only_rest_ids_returns_legacy_exactly(self, capsys):
        listing = [
            (_gid("Collection", 500), "Custom A"),
            (_gid("Collection", 501), "Custom, B"),
            (_gid("Collection", 700), "Smart più"),
        ]
        client = _make_client(FakeShop(listing, MEMBERS))
        result = client.build_product_collections_map()
        assert result == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "ok: 0 collections, 0 products"
        assert len(client._session.graphql_calls) == 1  # nessuna richiesta prodotti
        assert any("INFO collezioni nuovo modello: 0 collezioni, 0 prodotti" in l for l in _log_lines(capsys))

    def test_empty_listing_returns_legacy_exactly(self):
        result = _make_client(FakeShop([], MEMBERS)).build_product_collections_map()
        assert result == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "ok: 0 collections, 0 products"

    def test_new_collections_all_empty_returns_legacy_exactly(self):
        listing = [(_gid("Collection", 9001), "Vuota 1"), (_gid("Collection", 9002), "Vuota 2")]
        members = {_gid("Collection", 9001): [], _gid("Collection", 9002): []}
        result = _make_client(FakeShop(listing, members)).build_product_collections_map()
        assert result == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "ok: 2 collections, 0 products"


# --- Paginazione 0/1/2 pagine, sul listing e sui prodotti ---------------------

def _extra(**by_product):
    return {int(k[1:]): v for k, v in by_product.items()}


PAGINATION_CASES = {
    # id: (listing, members, extra atteso, chiamate GraphQL attese)
    "zero-collections": ([], {}, {}, 1),
    "one-collection-zero-members": (
        [(_gid("Collection", 9001), "T1")], {_gid("Collection", 9001): []}, {}, 2),
    "one-collection-one-member": (
        [(_gid("Collection", 9001), "T1")], {_gid("Collection", 9001): [21]},
        {21: ["T1"]}, 2),
    # esattamente una pagina piena (2 su size 2): hasNextPage false al confine
    "exact-page-boundary": (
        [(_gid("Collection", 9001), "T1"), (_gid("Collection", 9002), "T2")],
        {_gid("Collection", 9001): [21, 22], _gid("Collection", 9002): [22, 23]},
        {21: ["T1"], 22: ["T1", "T2"], 23: ["T2"]}, 3),
    # due pagine sul listing (2+1, con un id REST) e due sui prodotti (2+1)
    "two-pages-both": (
        [(_gid("Collection", 9003), "T3"), (_gid("Collection", 500), "Custom A"),
         (_gid("Collection", 9001), "T1"), (_gid("Collection", 9002), "T2")],
        {_gid("Collection", 9001): [21, 22, 23],
         _gid("Collection", 9002): [22, 23, 24],
         _gid("Collection", 9003): [23, 24, 25]},
        {21: ["T1"], 22: ["T1", "T2"], 23: ["T1", "T2", "T3"], 24: ["T2", "T3"], 25: ["T3"]},
        8),
}


class TestPagination:
    @pytest.mark.parametrize("name", list(PAGINATION_CASES))
    def test_pages_on_listing_and_products(self, name):
        listing, members, extra, calls = PAGINATION_CASES[name]
        client = _make_client(FakeShop(listing, members, listing_page=2, products_page=2))
        result = client.build_product_collections_map()
        assert result == {**LEGACY, **extra}
        assert len(client._session.graphql_calls) == calls

    def test_cursor_is_passed_on_next_page(self):
        listing, members, _, _ = PAGINATION_CASES["two-pages-both"]
        client = _make_client(FakeShop(listing, members, listing_page=2, products_page=2))
        client.build_product_collections_map()
        sent = [
            (_is_listing(p), p["variables"].get("id"), p["variables"]["cursor"])
            for _, p in client._session.graphql_calls
        ]
        assert sent == [
            (True, None, None),
            (True, None, "c2"),
            (False, "gid://shopify/Collection/9001", None),
            (False, "gid://shopify/Collection/9001", "c2"),
            (False, "gid://shopify/Collection/9002", None),
            (False, "gid://shopify/Collection/9002", "c2"),
            (False, "gid://shopify/Collection/9003", None),
            (False, "gid://shopify/Collection/9003", "c2"),
        ]

    def _long_listing(self, n):
        listing = [(_gid("Collection", 9000 + i), f"T{i}") for i in range(n)]
        members = {g: [] for g, _ in listing}
        return FakeShop(listing, members, listing_page=1, products_page=250)

    def test_exactly_the_page_cap_still_succeeds(self):
        client = _make_client(self._long_listing(100))
        result = client.build_product_collections_map()
        assert result == LEGACY
        assert shopify_client_module.get_new_model_collections_status() == "ok: 100 collections, 0 products"

    def test_one_page_over_the_cap_fails_soft(self):
        client = _make_client(self._long_listing(101))
        result = client.build_product_collections_map()
        assert result == LEGACY
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        # il passo si ferma al tetto: 100 richieste di listing, nessuna dei prodotti
        assert len(client._session.graphql_calls) == 100

    def test_cursor_that_never_advances_stops_at_the_cap(self):
        def stuck(url, payload):
            return FakeResponse({"data": {"collections": {
                "pageInfo": {"hasNextPage": True, "endCursor": "same"},
                "nodes": [{"id": _gid("Collection", 500), "title": "Custom A"}],
            }}})
        client = _make_client(responder=stuck)
        assert client.build_product_collections_map() == LEGACY
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        assert len(client._session.graphql_calls) == 100


# --- Fail-soft e all-or-nothing -----------------------------------------------

THROTTLED = {"errors": [{"message": "Throttled",
                         "extensions": {"code": "THROTTLED", "cost": {"requestedQueryCost": 100}}}]}


def _second_page_of_denim(payload):
    """Seconda pagina dei prodotti della SECONDA collezione nuova (DENIM)."""
    v = payload["variables"]
    return (not _is_listing(payload)
            and v["id"] == "gid://shopify/Collection/608803651916"
            and v["cursor"] == "c1")


def _first_listing(payload):
    return _is_listing(payload) and payload["variables"]["cursor"] is None


FAILURES = {
    "exception": lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    "errors-at-200": lambda: FakeResponse({"errors": [{"message": "Internal"}], "data": None}),
    "userErrors-shaped-errors": lambda: FakeResponse({"errors": [{"message": "x", "extensions": {"code": "ACCESS_DENIED"}}]}),
    "throttle-forever": lambda: FakeResponse(THROTTLED),
    "http-500": lambda: FakeResponse({"errors": "x"}, status_code=500),
    "http-503-forever": lambda: FakeResponse({}, status_code=503),
    "connection-error-forever": lambda: (_ for _ in ()).throw(requests.exceptions.ConnectionError("down")),
    "data-null": lambda: FakeResponse({"data": None}),
    "collection-null": lambda: FakeResponse({"data": {"collection": None}}),
    "products-null": lambda: FakeResponse({"data": {"collection": {"products": None}}}),
    "collections-null": lambda: FakeResponse({"data": {"collections": None}}),
}


class TestFailSoft:
    @pytest.mark.parametrize("where", ["first-listing", "second-page-of-second-collection"])
    @pytest.mark.parametrize("kind", list(FAILURES))
    def test_failure_returns_legacy_map_and_failed(self, kind, where, capsys):
        # collection-null/products-null hanno senso solo sui prodotti, ecc.:
        # un fallimento e' un fallimento in qualunque punto (all-or-nothing).
        shop = FakeShop(LISTING, MEMBERS, products_page=1)
        target = _first_listing if where == "first-listing" else _second_page_of_denim

        def responder(url, payload):
            if target(payload):
                return FAILURES[kind]()
            return shop.respond(url, payload)

        client = _make_client(responder=responder)
        result = client.build_product_collections_map()

        assert result == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        lines = _log_lines(capsys)
        assert len([l for l in lines if "ERROR collezioni nuovo modello" in l]) == 1
        assert not any("INFO collezioni nuovo modello" in l for l in lines)

    def test_midway_failure_really_had_partial_state_and_discards_it(self):
        # Prima del guasto la NUOVA (prima collezione) e' stata letta per intero e la
        # DENIM a meta': lo stato parziale esisteva, e non deve comparire nel risultato.
        shop = FakeShop(LISTING, MEMBERS, products_page=1)

        def responder(url, payload):
            if _second_page_of_denim(payload):
                return FakeResponse({"errors": [{"message": "Internal"}]})
            return shop.respond(url, payload)

        client = _make_client(responder=responder)
        result = client.build_product_collections_map()
        asked = [(p["variables"]["id"], p["variables"]["cursor"])
                 for _, p in client._session.graphql_calls if not _is_listing(p)]
        assert asked == [
            ("gid://shopify/Collection/77690241136", None),
            ("gid://shopify/Collection/77690241136", "c1"),
            ("gid://shopify/Collection/77690241136", "c2"),
            ("gid://shopify/Collection/608803651916", None),
            ("gid://shopify/Collection/608803651916", "c1"),
        ]
        for pid in (14, 15, 16):
            assert pid not in result
        assert NUOVA not in result[11] and DENIM not in result[11]
        assert result[11] == ["Custom A", "Smart più"]

    def test_throttle_exhaustion_is_five_attempts_and_four_throttles_recover(self):
        shop = FakeShop(LISTING, MEMBERS)
        state = {"n": 0}

        def throttle_then_ok(limit):
            def responder(url, payload):
                if _first_listing(payload) and state["n"] < limit:
                    state["n"] += 1
                    return FakeResponse(THROTTLED)
                return shop.respond(url, payload)
            return responder

        # 4 throttle + 1 ok: il retry di graphql() (max 5 tentativi) recupera.
        state["n"] = 0
        client = _make_client(responder=throttle_then_ok(4))
        assert client.build_product_collections_map() == MERGED
        assert shopify_client_module.get_new_model_collections_status() == STATUS_MERGED

        # 5 throttle: tentativi esauriti -> passo scartato.
        state["n"] = 0
        client = _make_client(responder=throttle_then_ok(5))
        assert client.build_product_collections_map() == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        assert len(client._session.graphql_calls) == 5

    @pytest.mark.parametrize("bad_id", [
        "gid://shopify/Product/5",          # tipo sbagliato
        "gid://shopify/Collection/",        # numero mancante
        "gid://shopify/Collection/12x",     # coda non numerica
        "gid://shopify/Collection/-5",
        "gid://shopify/Collection/ 5",
        "gid://shopify/Collection/٣",       # cifra non ASCII
        "Collection/5",
        "",
        None,
    ])
    def test_malformed_collection_gid_fails_soft(self, bad_id):
        shop = FakeShop([(bad_id, "Rotta"), (_gid("Collection", 9001), "T1")],
                        {_gid("Collection", 9001): [21]})
        client = _make_client(shop)
        assert client.build_product_collections_map() == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        # dopo il rifiuto nessuna richiesta prodotti (recorder, non uno stub che solleva)
        assert all(_is_listing(p) for _, p in client._session.graphql_calls)

    @pytest.mark.parametrize("bad_product_id", ["gid://shopify/Collection/21", "gid://shopify/Product/", "x"])
    def test_malformed_product_gid_fails_soft(self, bad_product_id):
        def responder(url, payload):
            if _is_listing(payload):
                return FakeResponse({"data": {"collections": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"id": _gid("Collection", 9001), "title": "T1"}]}}})
            return FakeResponse({"data": {"collection": {"products": {
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "nodes": [{"id": bad_product_id}]}}}})
        client = _make_client(responder=responder)
        assert client.build_product_collections_map() == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"

    @pytest.mark.parametrize("bad_title", [None, 5, ["x"]])
    def test_non_string_title_fails_soft(self, bad_title):
        shop = FakeShop([(_gid("Collection", 9001), bad_title)], {_gid("Collection", 9001): [21]})
        client = _make_client(shop)
        assert client.build_product_collections_map() == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"

    def test_has_next_page_without_cursor_fails_soft(self):
        def responder(url, payload):
            return FakeResponse({"data": {"collections": {
                "pageInfo": {"hasNextPage": True, "endCursor": None}, "nodes": []}}})
        client = _make_client(responder=responder)
        assert client.build_product_collections_map() == {
            11: ["Custom A", "Smart più"],
            12: ["Custom A", "Custom, B"],
            13: ["Smart più"],
        }
        assert shopify_client_module.get_new_model_collections_status() == "failed"
        assert len(client._session.graphql_calls) == 1

    def test_status_does_not_carry_over_between_runs(self):
        # ok -> failed -> ok: ogni run riscrive lo stato, mai quello del run precedente.
        _make_client().build_product_collections_map()
        assert shopify_client_module.get_new_model_collections_status() == STATUS_MERGED

        failing = _make_client(responder=lambda u, p: FakeResponse({"errors": [{"message": "x"}]}))
        failing.build_product_collections_map()
        assert shopify_client_module.get_new_model_collections_status() == "failed"

        _make_client().build_product_collections_map()
        assert shopify_client_module.get_new_model_collections_status() == STATUS_MERGED

    def test_rest_failure_still_raises_and_the_pass_never_runs(self):
        rest = dict(REST_FIXTURE)
        rest[f"{REST_BASE}/smart_collections.json?limit=250"] = lambda: FakeResponse({}, status_code=404)
        client = _make_client(rest=rest)
        with pytest.raises(requests.exceptions.HTTPError):
            client.build_product_collections_map()
        assert client._session.graphql_calls == []
        assert shopify_client_module.get_new_model_collections_status() is None


# --- Log ------------------------------------------------------------------------

class TestLog:
    def test_success_logs_one_info_line_and_no_error(self, capsys):
        _make_client().build_product_collections_map()
        lines = _log_lines(capsys)
        info = [l for l in lines if "INFO collezioni nuovo modello" in l]
        assert len(info) == 1
        assert info[0].endswith("INFO collezioni nuovo modello: 4 collezioni, 5 prodotti")
        assert not any("ERROR" in l for l in lines)

    def test_error_line_names_the_cause_without_secrets(self, capsys):
        client = _make_client(responder=lambda u, p: FakeResponse({"errors": [{"message": "boom-cause"}]}))
        client.build_product_collections_map()
        err = [l for l in _log_lines(capsys) if "ERROR collezioni nuovo modello" in l]
        assert len(err) == 1
        assert "boom-cause" in err[0]
        assert "X-Shopify-Access-Token" not in err[0]


# --- Il percorso legacy e il pin non cambiano -----------------------------------

class TestLegacyAndPinUnchanged:
    def test_rest_requests_are_exactly_the_legacy_ones(self):
        client = _make_client()
        client.build_product_collections_map()
        assert client._session.rest_calls == REST_CALLS_EXPECTED

    def test_new_pass_only_talks_to_2026_10_graphql(self):
        client = _make_client()
        client.build_product_collections_map()
        urls = {u for u, _ in client._session.graphql_calls}
        assert urls == {"https://shop.example.com/admin/api/2026-10/graphql.json"}
        assert all(u == GQL_NEW_URL for u, _ in client._session.graphql_calls)

    def test_service_pin_and_url_builders_unchanged(self):
        config = _make_config()
        assert config.api_version == "2024-04"
        assert config.api_url("products.json") == "https://shop.example.com/admin/api/2024-04/products.json"
        assert config.graphql_url() == "https://shop.example.com/admin/api/2024-04/graphql.json"
        assert config.new_model_collections_graphql_url() == "https://shop.example.com/admin/api/2026-10/graphql.json"
        assert NEW_MODEL_COLLECTIONS_API_VERSION == "2026-10"

    def test_new_url_does_not_follow_the_service_pin(self):
        config = _make_config()
        config.api_version = "2099-01"
        assert config.new_model_collections_graphql_url() == "https://shop.example.com/admin/api/2026-10/graphql.json"
        assert config.graphql_url() == "https://shop.example.com/admin/api/2099-01/graphql.json"

    def test_graphql_without_url_still_uses_the_service_pin(self):
        client = _make_client(responder=lambda u, p: FakeResponse({"data": {"ok": 1}}))
        assert client.graphql("query { x }") == {"ok": 1}
        assert client._session.graphql_calls[0][0] == GQL_PIN_URL

    def test_graphql_with_explicit_url_uses_it(self):
        client = _make_client(responder=lambda u, p: FakeResponse({"data": {"ok": 1}}))
        client.graphql("query { x }", url="https://example.test/gql")
        assert client._session.graphql_calls[0][0] == "https://example.test/gql"

    def test_queries_and_variables_verbatim(self):
        client = _make_client()
        client.build_product_collections_map()
        calls = client._session.graphql_calls
        first_query = calls[0][1]["query"]
        assert "collections(first: 250, after: $cursor)" in first_query
        assert "nodes {" in first_query and "id" in first_query and "title" in first_query
        assert calls[0][1]["variables"] == {"cursor": None}
        product_query = calls[1][1]["query"]
        assert "collection(id: $id)" in product_query
        assert "products(first: 250, after: $cursor)" in product_query
        assert calls[1][1]["variables"] == {"id": "gid://shopify/Collection/77690241136", "cursor": None}
        assert set(calls[0][1]) == {"query", "variables"}


# --- Cablaggio: il vero call site di shopify_to_mysql ---------------------------

def _product(pid, title="P"):
    return {
        "id": pid,
        "title": title,
        "handle": f"p-{pid}",
        "vendor": "V",
        "product_type": "T",
        "tags": "sneakers personalizzate",
        "body_html": "<p>x</p>",
        "images": [],
        "image": None,
        "metafields": {},
        "variants": [{
            "id": pid * 10,
            "title": "42",
            "sku": f"SKU-{pid}",
            "barcode": "",
            "price": "49.90",
            "compare_at_price": None,
            "inventory_item_id": 1,
            "stock_for_location": 3,
        }],
    }


class TestPipelineEndToEnd:
    def _run(self, client, product_ids):
        client.get_products_graphql = lambda **kw: iter([_product(p) for p in product_ids])
        db = MagicMock()
        db.get_existing_variant_ids.return_value = set()
        db.get_variant_prices.return_value = None
        shopify_to_mysql.sync_products_graphql(_make_config(), client, db)
        return {c.kwargs["product_id"]: c.kwargs["collections"] for c in db.upsert_product.call_args_list}

    def test_real_call_site_joins_the_merged_map(self):
        written = self._run(_make_client(), [11, 12, 13, 14, 15, 99])
        assert written == {
            11: "Custom A, Smart più, NUOVA COLLEZIONE 2026, Scarpe in Jeans Denim",
            12: "Custom A, Custom, B, Scarpe in Jeans Denim",
            13: "Smart più",
            14: "NUOVA COLLEZIONE 2026",
            15: "Scarpe in Jeans Denim, Scarpe personalizzate più di tendenza, e alla moda",
            99: "",
        }

    def test_pass_failure_writes_exactly_todays_strings(self):
        failing = _make_client(responder=lambda u, p: FakeResponse({"errors": [{"message": "x"}]}))
        written = self._run(failing, [11, 12, 13, 14, 99])
        assert written == {
            11: "Custom A, Smart più",
            12: "Custom A, Custom, B",
            13: "Smart più",
            14: "",
            99: "",
        }


# --- /api/status e run_sync -----------------------------------------------------

@pytest.fixture
def app_module():
    os.environ.pop("TRIGGER_SECRET", None)
    import app as app_mod
    importlib.reload(app_mod)
    app_mod.app.config["TESTING"] = True
    return app_mod


class TestApiStatus:
    def test_status_exposes_the_field_before_any_run(self, app_module):
        with app_module.app.test_client() as c:
            body = c.get("/api/status").get_json()
        assert body["new_model_collections"] is None
        assert set(body) == {
            "running", "last_run", "last_status", "last_duration", "last_error", "new_model_collections",
        }

    def _fake_main(self, responder):
        def main():
            ShopifyClient_ = shopify_client_module.ShopifyClient
            client = ShopifyClient_(_make_config())
            client._session = FakeSession(REST_FIXTURE, responder)
            client.build_product_collections_map()
        return main

    def test_run_sync_publishes_ok_status(self, app_module, monkeypatch):
        monkeypatch.setattr(shopify_to_mysql, "main", self._fake_main(FakeShop(LISTING, MEMBERS).respond))
        app_module.run_sync()
        with app_module.app.test_client() as c:
            body = c.get("/api/status").get_json()
        assert body["new_model_collections"] == "ok: 4 collections, 5 products"
        assert body["last_status"] == "success"

    def test_run_sync_publishes_failed_status(self, app_module, monkeypatch):
        monkeypatch.setattr(
            shopify_to_mysql, "main",
            self._fake_main(lambda u, p: FakeResponse({"errors": [{"message": "x"}]})),
        )
        app_module.run_sync()
        with app_module.app.test_client() as c:
            body = c.get("/api/status").get_json()
        assert body["new_model_collections"] == "failed"
        assert body["last_status"] == "success"  # il sync prosegue con la mappa legacy

    def test_run_sync_status_survives_a_crash_after_the_pass(self, app_module, monkeypatch):
        inner = self._fake_main(FakeShop(LISTING, MEMBERS).respond)

        def main():
            inner()
            raise RuntimeError("db down")
        monkeypatch.setattr(shopify_to_mysql, "main", main)
        app_module.run_sync()
        assert app_module.sync_status["new_model_collections"] == "ok: 4 collections, 5 products"
        assert app_module.sync_status["last_status"] == "failed"

    def test_run_sync_clears_the_previous_value_at_start(self, app_module, monkeypatch):
        app_module.sync_status["new_model_collections"] = "ok: 9 collections, 9 products"
        shopify_client_module.set_new_model_collections_status("ok: 9 collections, 9 products")
        seen = {}

        def main():
            seen["during"] = app_module.sync_status["new_model_collections"]
            seen["module_during"] = shopify_client_module.get_new_model_collections_status()
        monkeypatch.setattr(shopify_to_mysql, "main", main)
        app_module.run_sync()
        assert seen == {"during": None, "module_during": None}
        assert app_module.sync_status["new_model_collections"] is None
