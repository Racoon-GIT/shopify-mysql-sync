# src/shopify_client.py
"""
Client Shopify con gestione robusta di rate limiting e retry.
"""

import time
import json as json_module  # Evita shadowing con parametro 'json'
from typing import Optional, Dict, Any, List
from collections import defaultdict

import requests

from .config import Config, log


class ShopifyClient:
    """Client per Shopify Admin REST API con retry e rate limiting."""

    # Codici HTTP che richiedono retry
    RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

    # Sleep tra chiamate consecutive (Shopify permette 2 req/sec)
    DEFAULT_SLEEP = 0.5

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
