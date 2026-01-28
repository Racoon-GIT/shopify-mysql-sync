# src/shopify_client.py
"""
Client Shopify con gestione robusta di rate limiting e retry.
Supporta sia REST API che GraphQL Admin API.
"""

import time
import json as json_module  # Evita shadowing con parametro 'json'
from typing import Optional, Dict, Any, List, Generator
from collections import defaultdict

import requests

from .config import Config, log


class ShopifyClient:
    """Client per Shopify Admin REST e GraphQL API con retry e rate limiting."""

    # Codici HTTP che richiedono retry
    RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

    # Sleep tra chiamate consecutive (Shopify permette 2 req/sec)
    DEFAULT_SLEEP = 0.5

    # Query GraphQL per prodotti con varianti, metafield e immagini
    # Limite: 10 prodotti per pagina per restare sotto 1000 punti di costo
    # Costo stimato: ~30 punti base + (10 prod × ~80 punti) = ~830 punti
    GRAPHQL_PRODUCTS_QUERY = """
    query GetProducts($cursor: String, $query: String) {
        products(first: 10, after: $cursor, query: $query) {
            pageInfo {
                hasNextPage
                endCursor
            }
            edges {
                node {
                    id
                    legacyResourceId
                    title
                    handle
                    vendor
                    tags
                    descriptionHtml
                    status
                    featuredImage {
                        url
                        altText
                        width
                        height
                    }
                    images(first: 10) {
                        edges {
                            node {
                                id
                                url
                                altText
                                width
                                height
                            }
                        }
                    }
                    metafields(first: 10) {
                        edges {
                            node {
                                namespace
                                key
                                value
                            }
                        }
                    }
                    variants(first: 50) {
                        edges {
                            node {
                                id
                                legacyResourceId
                                title
                                sku
                                barcode
                                price
                                compareAtPrice
                                inventoryItem {
                                    id
                                    legacyResourceId
                                    inventoryLevels(first: 5) {
                                        edges {
                                            node {
                                                location {
                                                    name
                                                }
                                                quantities(names: ["available"]) {
                                                    name
                                                    quantity
                                                }
                                            }
                                        }
                                    }
                                }
                                metafields(first: 15) {
                                    edges {
                                        node {
                                            namespace
                                            key
                                            value
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """

    def __init__(self, config: Config):
        """
        Inizializza il client Shopify.

        Args:
            config: Configurazione dell'applicazione
        """
        self.config = config
        self._session = requests.Session()
        self._session.headers.update(config.headers)

    def _request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        max_retries: int = 5,
        full_url: Optional[str] = None
    ) -> requests.Response:
        """
        Esegue una richiesta HTTP con retry e gestione rate limiting.

        Args:
            method: Metodo HTTP (GET, POST, PUT, DELETE)
            endpoint: Endpoint relativo (ignorato se full_url è fornito)
            payload: Body della richiesta (per POST/PUT)
            params: Query parameters
            max_retries: Numero massimo di tentativi
            full_url: URL completo (per paginazione)

        Returns:
            requests.Response: Risposta HTTP

        Raises:
            Exception: Se tutti i tentativi falliscono
        """
        url = full_url if full_url else self.config.api_url(endpoint)

        for attempt in range(max_retries):
            try:
                response = self._session.request(
                    method=method,
                    url=url,
                    json=payload,
                    params=params
                )

                # Rate limit o errore transitorio
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    wait_time = self._calculate_wait_time(response, attempt)
                    log(f"⏳ Retry {attempt + 1}/{max_retries} - Status {response.status_code} - Attendo {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    continue

                # Errore client/server non transitorio
                if response.status_code >= 400:
                    self._log_error(response)

                response.raise_for_status()
                return response

            except requests.exceptions.ConnectionError as e:
                wait_time = 2 ** attempt
                log(f"⚠️ Errore connessione, retry {attempt + 1}/{max_retries} in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue

        raise Exception(f"❌ Richiesta fallita dopo {max_retries} tentativi: {url}")

    def _calculate_wait_time(self, response: requests.Response, attempt: int) -> float:
        """
        Calcola tempo di attesa per retry.

        Args:
            response: Risposta HTTP
            attempt: Numero tentativo corrente

        Returns:
            float: Secondi da attendere
        """
        if response.status_code == 429:
            # Usa Retry-After header se disponibile
            retry_after = response.headers.get("Retry-After", "2")
            try:
                return float(retry_after)
            except ValueError:
                pass
        # Exponential backoff per altri errori
        return min(2 ** attempt, 32)  # Max 32 secondi

    def _log_error(self, response: requests.Response) -> None:
        """Logga dettagli errore API."""
        try:
            error_detail = response.json()
            log(f"❌ Errore API {response.status_code}: {json_module.dumps(error_detail, indent=2)}")
        except Exception:
            log(f"❌ Errore API {response.status_code}: {response.text}")

    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        full_url: Optional[str] = None
    ) -> requests.Response:
        """GET request."""
        return self._request("GET", endpoint, params=params, full_url=full_url)

    def post(
        self,
        endpoint: str,
        payload: Dict[str, Any]
    ) -> requests.Response:
        """POST request."""
        response = self._request("POST", endpoint, payload=payload)
        time.sleep(self.DEFAULT_SLEEP)
        return response

    def put(
        self,
        endpoint: str,
        payload: Dict[str, Any]
    ) -> requests.Response:
        """PUT request."""
        response = self._request("PUT", endpoint, payload=payload)
        time.sleep(self.DEFAULT_SLEEP)
        return response

    def delete(self, endpoint: str) -> requests.Response:
        """DELETE request."""
        response = self._request("DELETE", endpoint)
        time.sleep(self.DEFAULT_SLEEP)
        return response

    # --- GraphQL Methods ---

    def graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        max_retries: int = 5
    ) -> Dict[str, Any]:
        """
        Esegue una query GraphQL Admin API.

        Args:
            query: Query GraphQL
            variables: Variabili della query
            max_retries: Numero massimo di tentativi

        Returns:
            Dict: Risposta JSON (campo 'data')

        Raises:
            Exception: Se la query fallisce
        """
        url = self.config.graphql_url()
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        for attempt in range(max_retries):
            try:
                response = self._session.post(url, json=payload)

                # Rate limit o errore transitorio
                if response.status_code in self.RETRYABLE_STATUS_CODES:
                    wait_time = self._calculate_wait_time(response, attempt)
                    log(f"⏳ GraphQL Retry {attempt + 1}/{max_retries} - Status {response.status_code} - Attendo {wait_time:.1f}s...")
                    time.sleep(wait_time)
                    continue

                if response.status_code >= 400:
                    self._log_error(response)
                    response.raise_for_status()

                result = response.json()

                # Controlla errori GraphQL
                if "errors" in result:
                    errors = result["errors"]
                    # Controlla se è throttled
                    for err in errors:
                        if "THROTTLED" in str(err.get("extensions", {})):
                            cost = err.get("extensions", {}).get("cost", {})
                            wait_time = cost.get("requestedQueryCost", 10) / 50  # ~50 points/sec
                            log(f"⏳ GraphQL throttled, attendo {wait_time:.1f}s...")
                            time.sleep(max(wait_time, 2))
                            continue
                    # Altri errori
                    error_msgs = [e.get("message", str(e)) for e in errors]
                    raise Exception(f"GraphQL errors: {'; '.join(error_msgs)}")

                return result.get("data", {})

            except requests.exceptions.ConnectionError as e:
                wait_time = 2 ** attempt
                log(f"⚠️ Errore connessione GraphQL, retry {attempt + 1}/{max_retries} in {wait_time}s: {e}")
                time.sleep(wait_time)
                continue

        raise Exception(f"❌ Query GraphQL fallita dopo {max_retries} tentativi")

    def get_products_graphql(
        self,
        status: str = "active",
        location_name: Optional[str] = None
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Generatore che recupera prodotti via GraphQL con metafield e varianti inclusi.
        Molto più efficiente di REST (1 chiamata vs N chiamate per metafield).

        Args:
            status: Stato prodotti (active, draft, archived)
            location_name: Nome location per filtrare inventory (es. "Magazzino")

        Yields:
            Dict: Prodotto normalizzato con struttura simile a REST + metafield
        """
        cursor = None
        page = 0
        query_filter = f"status:{status}"

        while True:
            page += 1
            variables = {"cursor": cursor, "query": query_filter}

            log(f"📡 GraphQL: Recupero pagina {page}...")
            data = self.graphql(self.GRAPHQL_PRODUCTS_QUERY, variables)

            products_data = data.get("products", {})
            edges = products_data.get("edges", [])
            page_info = products_data.get("pageInfo", {})

            for edge in edges:
                node = edge["node"]
                yield self._normalize_graphql_product(node, location_name)

            # Paginazione
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

    def _normalize_graphql_product(
        self,
        node: Dict[str, Any],
        location_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Normalizza risposta GraphQL in formato compatibile con REST.

        Args:
            node: Nodo prodotto GraphQL
            location_name: Nome location per filtrare stock

        Returns:
            Dict: Prodotto normalizzato
        """
        # Estrai ID numerico da GID
        product_id = int(node["legacyResourceId"])

        # Immagini
        images = []
        for idx, img_edge in enumerate(node.get("images", {}).get("edges", [])):
            img = img_edge["node"]
            # Estrai ID numerico da GID immagine
            img_gid = img.get("id", "")
            img_id = int(img_gid.split("/")[-1]) if img_gid else None

            images.append({
                "id": img_id,
                "position": idx + 1,
                "src": img.get("url", ""),
                "alt": img.get("altText") or "",
                "width": img.get("width"),
                "height": img.get("height")
            })

        # Featured image
        featured = node.get("featuredImage")
        featured_image = None
        if featured:
            featured_image = {
                "src": featured.get("url", ""),
                "alt": featured.get("altText") or "",
                "width": featured.get("width"),
                "height": featured.get("height")
            }

        # Metafield prodotto
        product_metafields = {}
        for mf_edge in node.get("metafields", {}).get("edges", []):
            mf = mf_edge["node"]
            key = f"{mf['namespace']}.{mf['key']}"
            product_metafields[key] = mf.get("value")

        # Varianti
        variants = []
        for var_edge in node.get("variants", {}).get("edges", []):
            var = var_edge["node"]
            variant_id = int(var["legacyResourceId"])

            # Inventory item
            inv_item = var.get("inventoryItem", {})
            inventory_item_id = int(inv_item.get("legacyResourceId", 0)) if inv_item.get("legacyResourceId") else 0

            # Stock per location specifica
            stock_for_location = None
            if location_name:
                for level_edge in inv_item.get("inventoryLevels", {}).get("edges", []):
                    level = level_edge["node"]
                    loc = level.get("location", {})
                    if loc.get("name", "").lower() == location_name.lower():
                        quantities = level.get("quantities", [])
                        for q in quantities:
                            if q.get("name") == "available":
                                stock_for_location = q.get("quantity")
                                break
                        break

            # Metafield variante
            variant_metafields = {}
            for mf_edge in var.get("metafields", {}).get("edges", []):
                mf = mf_edge["node"]
                key = f"{mf['namespace']}.{mf['key']}"
                variant_metafields[key] = mf.get("value")

            variants.append({
                "id": variant_id,
                "title": var.get("title", ""),
                "sku": var.get("sku") or "",
                "barcode": var.get("barcode") or "",
                "price": var.get("price", "0"),
                "compare_at_price": var.get("compareAtPrice") or "0",
                "inventory_item_id": inventory_item_id,
                "stock_for_location": stock_for_location,
                "metafields": variant_metafields
            })

        return {
            "id": product_id,
            "title": node.get("title", ""),
            "handle": node.get("handle", ""),
            "vendor": node.get("vendor", ""),
            "tags": ", ".join(node.get("tags", [])),
            "body_html": node.get("descriptionHtml"),
            "status": node.get("status", "").lower(),
            "images": images,
            "image": featured_image,
            "variants": variants,
            "metafields": product_metafields
        }

    # --- Metodi di utilità ---

    @staticmethod
    def extract_next_link(link_header: Optional[str]) -> Optional[str]:
        """
        Estrae URL per pagina successiva da Link header.

        Args:
            link_header: Valore header Link

        Returns:
            Optional[str]: URL pagina successiva o None
        """
        if not link_header:
            return None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                return part.split(";")[0].strip("<> ")
        return None

    # --- Metodi specifici Shopify ---

    def get_products(self, status: str = "active", limit: int = 250):
        """
        Generatore che itera su tutti i prodotti con paginazione.

        Args:
            status: Stato prodotti (active, draft, archived)
            limit: Prodotti per pagina (max 250)

        Yields:
            dict: Singolo prodotto
        """
        url = self.config.api_url(f"products.json?status={status}&limit={limit}")

        while url:
            response = self.get("", full_url=url)
            products = response.json().get("products", [])

            for product in products:
                yield product

            url = self.extract_next_link(response.headers.get("Link"))

    def get_product_variants(self, product_id: int) -> List[Dict[str, Any]]:
        """
        Recupera tutte le varianti di un prodotto.

        Args:
            product_id: ID prodotto

        Returns:
            List[Dict]: Lista varianti
        """
        response = self.get(f"products/{product_id}/variants.json")
        return response.json().get("variants", [])

    def create_variant(self, product_id: int, variant_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Crea una nuova variante per un prodotto.

        Args:
            product_id: ID prodotto
            variant_data: Dati variante

        Returns:
            Dict: Variante creata
        """
        response = self.post(
            f"products/{product_id}/variants.json",
            {"variant": variant_data}
        )
        return response.json().get("variant", {})

    def delete_variant(self, product_id: int, variant_id: int) -> bool:
        """
        Elimina una variante.

        Args:
            product_id: ID prodotto
            variant_id: ID variante

        Returns:
            bool: True se eliminata con successo
        """
        try:
            self.delete(f"products/{product_id}/variants/{variant_id}.json")
            return True
        except Exception as e:
            log(f"❌ Errore eliminazione variante {variant_id}: {e}")
            return False

    def get_inventory_levels(self, inventory_item_id: int) -> List[Dict[str, Any]]:
        """
        Recupera inventory levels per un inventory item.

        Args:
            inventory_item_id: ID inventory item

        Returns:
            List[Dict]: Lista inventory levels
        """
        try:
            response = self.get(
                "inventory_levels.json",
                params={"inventory_item_ids": inventory_item_id}
            )
            return response.json().get("inventory_levels", [])
        except Exception as e:
            log(f"⚠️ Errore recupero inventory levels: {e}")
            return []

    def set_inventory_level(
        self,
        inventory_item_id: int,
        location_id: int,
        available: int
    ) -> bool:
        """
        Imposta inventory level per una location.

        Args:
            inventory_item_id: ID inventory item
            location_id: ID location
            available: Quantità disponibile

        Returns:
            bool: True se impostato con successo
        """
        try:
            self.post(
                "inventory_levels/set.json",
                {
                    "location_id": location_id,
                    "inventory_item_id": inventory_item_id,
                    "available": available
                }
            )
            log(f"  ✅ Inventory impostato: location {location_id} → {available} unità")
            return True
        except Exception as e:
            log(f"  ❌ Errore impostazione inventory: {e}")
            return False

    def remove_inventory_level(self, inventory_item_id: int, location_id: int) -> bool:
        """
        Rimuove inventory level per una location.

        Args:
            inventory_item_id: ID inventory item
            location_id: ID location

        Returns:
            bool: True se rimosso con successo
        """
        try:
            self.delete(
                f"inventory_levels.json?inventory_item_id={inventory_item_id}&location_id={location_id}"
            )
            log(f"  🗑️ Location {location_id} rimossa")
            return True
        except Exception as e:
            log(f"  ❌ Errore rimozione inventory level: {e}")
            return False

    def build_product_collections_map(self) -> Dict[int, List[str]]:
        """
        Costruisce mappa prodotto -> collezioni.

        Returns:
            Dict[int, List[str]]: {product_id: [collection_title1, ...]}
        """
        product_to_collections: Dict[int, List[str]] = defaultdict(list)

        def fetch_collections(endpoint: str):
            url = self.config.api_url(f"{endpoint}?limit=250")

            while url:
                response = self.get("", full_url=url)
                key = endpoint.split(".")[0]
                collections = response.json().get(key, [])

                for coll in collections:
                    coll_id = coll["id"]
                    title = coll["title"]

                    # Recupera prodotti nella collezione
                    prod_url = self.config.api_url(f"collections/{coll_id}/products.json?limit=250")

                    while prod_url:
                        prod_response = self.get("", full_url=prod_url)
                        for product in prod_response.json().get("products", []):
                            product_to_collections[product["id"]].append(title)
                        prod_url = self.extract_next_link(prod_response.headers.get("Link"))

                url = self.extract_next_link(response.headers.get("Link"))

        log("🔁 Caricamento mappa collezioni da custom_collections...")
        fetch_collections("custom_collections.json")

        log("🔁 Caricamento mappa collezioni da smart_collections...")
        fetch_collections("smart_collections.json")

        log(f"✅ Mappa collezioni creata con {len(product_to_collections)} prodotti.")
        return dict(product_to_collections)

    def get_locations(self) -> List[Dict[str, Any]]:
        """
        Recupera tutte le locations dello store.

        Returns:
            List[Dict]: Lista locations
        """
        try:
            response = self.get("locations.json")
            return response.json().get("locations", [])
        except Exception as e:
            log(f"⚠️ Errore recupero locations: {e}")
            return []

    def get_location_id_by_name(self, name: str) -> Optional[int]:
        """
        Trova l'ID di una location dato il nome.

        Args:
            name: Nome della location (case-insensitive)

        Returns:
            Optional[int]: ID location o None se non trovata
        """
        locations = self.get_locations()
        name_lower = name.lower()

        for loc in locations:
            if loc.get("name", "").lower() == name_lower:
                return loc["id"]

        log(f"⚠️ Location '{name}' non trovata")
        return None

    def get_inventory_level_for_location(
        self,
        inventory_item_id: int,
        location_id: int
    ) -> Optional[int]:
        """
        Recupera la quantità disponibile per una specifica location.

        Args:
            inventory_item_id: ID inventory item
            location_id: ID location

        Returns:
            Optional[int]: Quantità disponibile o None se non trovata
        """
        levels = self.get_inventory_levels(inventory_item_id)

        for level in levels:
            if level.get("location_id") == location_id:
                return level.get("available")

        return None

    def build_inventory_map_for_location(
        self,
        inventory_item_ids: List[int],
        location_id: int
    ) -> Dict[int, Optional[int]]:
        """
        Costruisce mappa inventory_item_id -> quantità per una location.
        Usa batch API per efficienza.

        Args:
            inventory_item_ids: Lista di inventory item IDs
            location_id: ID location

        Returns:
            Dict[int, Optional[int]]: {inventory_item_id: available}
        """
        result: Dict[int, Optional[int]] = {}

        # Shopify permette max 50 inventory_item_ids per chiamata
        batch_size = 50

        for i in range(0, len(inventory_item_ids), batch_size):
            batch = inventory_item_ids[i:i + batch_size]
            ids_param = ",".join(str(id_) for id_ in batch)

            try:
                response = self.get(
                    "inventory_levels.json",
                    params={
                        "inventory_item_ids": ids_param,
                        "location_ids": location_id
                    }
                )
                levels = response.json().get("inventory_levels", [])

                # Mappa risultati
                for level in levels:
                    result[level["inventory_item_id"]] = level.get("available")

                # Segna come None quelli non trovati
                for item_id in batch:
                    if item_id not in result:
                        result[item_id] = None

            except Exception as e:
                log(f"⚠️ Errore recupero batch inventory: {e}")
                for item_id in batch:
                    result[item_id] = None

        return result

    # --- Metodi per Metafield ---

    def get_product_metafields(self, product_id: int) -> Dict[str, Any]:
        """
        Recupera tutti i metafield di un prodotto.

        Args:
            product_id: ID prodotto

        Returns:
            Dict[str, Any]: {namespace.key: value}
        """
        result = {}
        try:
            response = self.get(f"products/{product_id}/metafields.json")
            metafields = response.json().get("metafields", [])

            for mf in metafields:
                key = f"{mf['namespace']}.{mf['key']}"
                result[key] = mf.get("value")

        except Exception as e:
            log(f"⚠️ Errore recupero metafield prodotto {product_id}: {e}")

        return result

    def get_variant_metafields(self, variant_id: int) -> Dict[str, Any]:
        """
        Recupera tutti i metafield di una variante.

        Args:
            variant_id: ID variante

        Returns:
            Dict[str, Any]: {namespace.key: value}
        """
        result = {}
        try:
            response = self.get(f"variants/{variant_id}/metafields.json")
            metafields = response.json().get("metafields", [])

            for mf in metafields:
                key = f"{mf['namespace']}.{mf['key']}"
                result[key] = mf.get("value")

        except Exception as e:
            log(f"⚠️ Errore recupero metafield variante {variant_id}: {e}")

        return result

    @staticmethod
    def extract_product_metafields(metafields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estrae i metafield prodotto rilevanti in un dizionario normalizzato.

        Args:
            metafields: Dizionario {namespace.key: value}

        Returns:
            Dict con chiavi normalizzate per DB
        """
        # Mapping namespace.key -> nome campo DB
        mapping = {
            "custom.customization_description": "customization_description",
            "custom.shoe_details": "shoe_details",
            "custom.customization_details": "customization_details",
            "custom.o_description": "o_description",
            "custom.handling": "handling",
            "mm-google-shopping.is_custom_product": "google_custom_product",
        }

        result = {}
        for mf_key, db_key in mapping.items():
            value = metafields.get(mf_key)
            if value is not None:
                # Converti handling in int se presente
                if db_key == "handling":
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        value = None
                # Converti boolean per google_custom_product
                elif db_key == "google_custom_product":
                    value = str(value).lower() in ("true", "1", "yes")
                result[db_key] = value

        return result

    @staticmethod
    def extract_variant_metafields(metafields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Estrae i metafield variante Google Shopping in un dizionario normalizzato.

        Args:
            metafields: Dizionario {namespace.key: value}

        Returns:
            Dict con chiavi normalizzate per DB
        """
        # Mapping namespace.key -> nome campo DB
        mapping = {
            "mm-google-shopping.age_group": "google_age_group",
            "mm-google-shopping.condition": "google_condition",
            "mm-google-shopping.gender": "google_gender",
            "mm-google-shopping.mpn": "google_mpn",
            "mm-google-shopping.custom_label_0": "google_custom_label_0",
            "mm-google-shopping.custom_label_1": "google_custom_label_1",
            "mm-google-shopping.custom_label_2": "google_custom_label_2",
            "mm-google-shopping.custom_label_3": "google_custom_label_3",
            "mm-google-shopping.custom_label_4": "google_custom_label_4",
            "mm-google-shopping.size_system": "google_size_system",
            "mm-google-shopping.size_type": "google_size_type",
        }

        result = {}
        for mf_key, db_key in mapping.items():
            value = metafields.get(mf_key)
            if value is not None:
                result[db_key] = value

        return result

    @staticmethod
    def build_images_json(product: Dict[str, Any]) -> str:
        """
        Costruisce JSON delle immagini prodotto.

        Args:
            product: Dati prodotto da Shopify

        Returns:
            str: JSON string con struttura immagini
        """
        images = product.get("images", [])
        image_data = {
            "count": len(images),
            "images": []
        }

        for img in images:
            # Estrai nome file da URL
            src = img.get("src", "")
            filename = src.split("/")[-1].split("?")[0] if src else ""

            image_data["images"].append({
                "position": img.get("position"),
                "src": filename,
                "alt": img.get("alt") or "",
                "width": img.get("width"),
                "height": img.get("height"),
                "id": img.get("id")
            })

        # Featured image
        if product.get("image"):
            featured_src = product["image"].get("src", "")
            image_data["featured"] = featured_src.split("/")[-1].split("?")[0] if featured_src else ""

        return json_module.dumps(image_data, ensure_ascii=False)
