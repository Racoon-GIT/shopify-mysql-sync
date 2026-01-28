# src/config.py
"""
Configurazione centralizzata con validazione delle variabili d'ambiente.
"""

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, List


def log(msg: str) -> None:
    """Log con timestamp formattato."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


@dataclass
class Config:
    """Configurazione centralizzata dell'applicazione."""

    # Shopify
    shop_domain: str
    access_token: str
    api_version: str

    # Database
    db_host: str
    db_user: str
    db_pass: str
    db_name: str

    # Opzionali
    product_ids: Optional[List[str]] = None
    debug: bool = True

    # Tag validi per filtro prodotti (usato in sync)
    VALID_TAGS = {
        "sneakers personalizzate",
        "scarpe personalizzate",
        "ciabatte personalizzate",
        "stivali personalizzati"
    }

    @classmethod
    def from_env(cls, require_product_ids: bool = False) -> 'Config':
        """
        Carica configurazione da variabili d'ambiente con validazione.

        Args:
            require_product_ids: Se True, richiede PRODUCT_IDS (per reset_variants)

        Returns:
            Config: Istanza configurata

        Raises:
            SystemExit: Se mancano variabili obbligatorie
        """
        errors = []

        # Variabili obbligatorie Shopify
        shop_domain = os.getenv("SHOPIFY_DOMAIN")
        if not shop_domain:
            errors.append("SHOPIFY_DOMAIN")

        access_token = os.getenv("SHOPIFY_TOKEN")
        if not access_token:
            errors.append("SHOPIFY_TOKEN")

        # Variabili obbligatorie Database
        db_host = os.getenv("DB_HOST")
        if not db_host:
            errors.append("DB_HOST")

        db_user = os.getenv("DB_USER")
        if not db_user:
            errors.append("DB_USER")

        db_pass = os.getenv("DB_PASS")
        if not db_pass:
            errors.append("DB_PASS")

        db_name = os.getenv("DB_NAME")
        if not db_name:
            errors.append("DB_NAME")

        # Product IDs (opzionale o obbligatorio)
        product_ids_env = os.getenv("PRODUCT_IDS")
        product_ids = None
        if product_ids_env:
            product_ids = [pid.strip() for pid in product_ids_env.split(",") if pid.strip()]
        elif require_product_ids:
            errors.append("PRODUCT_IDS")

        # Se ci sono errori, log e exit
        if errors:
            log(f"❌ Variabili d'ambiente mancanti: {', '.join(errors)}")
            sys.exit(1)

        # API Version (con default)
        api_version = os.getenv("SHOPIFY_API_VERSION", "2024-04")

        # Debug mode
        debug = os.getenv("DEBUG", "true").lower() in ("true", "1", "yes")

        return cls(
            shop_domain=shop_domain,
            access_token=access_token,
            api_version=api_version,
            db_host=db_host,
            db_user=db_user,
            db_pass=db_pass,
            db_name=db_name,
            product_ids=product_ids,
            debug=debug
        )

    @property
    def headers(self) -> dict:
        """Headers per chiamate API Shopify."""
        return {
            "X-Shopify-Access-Token": self.access_token,
            "Content-Type": "application/json"
        }

    def api_url(self, endpoint: str) -> str:
        """
        Costruisce URL completo per endpoint REST Shopify.

        Args:
            endpoint: Endpoint relativo (es. "products.json")

        Returns:
            str: URL completo
        """
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/{endpoint}"

    def graphql_url(self) -> str:
        """
        Costruisce URL per GraphQL Admin API.

        Returns:
            str: URL GraphQL endpoint
        """
        return f"https://{self.shop_domain}/admin/api/{self.api_version}/graphql.json"
