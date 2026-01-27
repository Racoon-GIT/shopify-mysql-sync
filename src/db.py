# src/db.py
"""
Gestione database MySQL centralizzata.
"""

from typing import Optional, List, Tuple, Any, Set
from decimal import Decimal
from contextlib import contextmanager

import mysql.connector
from mysql.connector import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from .config import Config, log


class Database:
    """Gestione connessione e operazioni database MySQL."""

    # DDL per tabella prodotti online
    DDL_ONLINE_PRODUCTS = """
    CREATE TABLE IF NOT EXISTS online_products (
        Variant_id        BIGINT PRIMARY KEY,
        Variant_Title     TEXT,
        SKU               VARCHAR(255),
        Barcode           VARCHAR(255),
        Product_id        BIGINT,
        Product_title     TEXT,
        Product_handle    VARCHAR(255),
        Vendor            VARCHAR(255),
        Price             DECIMAL(10,2),
        Compare_AT_Price  DECIMAL(10,2),
        Inventory_Item_ID BIGINT,
        Stock_Magazzino   INT DEFAULT NULL,
        Tags              TEXT,
        Collections       TEXT
    )
    """

    # ALTER TABLE per aggiungere colonna (senza IF NOT EXISTS per compatibilità MySQL)
    ALTER_ADD_STOCK_MAGAZZINO = """
    ALTER TABLE online_products
    ADD COLUMN Stock_Magazzino INT DEFAULT NULL AFTER Inventory_Item_ID
    """

    # DDL per storico prezzi
    DDL_PRICE_HISTORY = """
    CREATE TABLE IF NOT EXISTS price_history (
        id                BIGINT AUTO_INCREMENT PRIMARY KEY,
        Variant_id        BIGINT,
        Old_Price         DECIMAL(10,2),
        New_Price         DECIMAL(10,2),
        Old_Compare_AT    DECIMAL(10,2),
        New_Compare_AT    DECIMAL(10,2),
        changed_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """

    # DDL per backup varianti (tabella temporanea)
    DDL_VARIANT_BACKUP = """
    CREATE TEMPORARY TABLE IF NOT EXISTS variant_backup (
        id BIGINT,
        product_id BIGINT,
        inventory_item_id BIGINT,
        variant_json TEXT,
        position INT,
        PRIMARY KEY (product_id, id)
    )
    """

    # DDL per backup inventory (tabella temporanea)
    DDL_INVENTORY_BACKUP = """
    CREATE TEMPORARY TABLE IF NOT EXISTS inventory_backup (
        variant_id BIGINT,
        inventory_item_id BIGINT,
        location_id BIGINT,
        available INT,
        PRIMARY KEY (variant_id, location_id)
    )
    """

    def __init__(self, config: Config):
        """
        Inizializza la connessione database.

        Args:
            config: Configurazione dell'applicazione
        """
        self.config = config
        self._connection: Optional[MySQLConnection] = None
        self._cursor: Optional[MySQLCursor] = None

    def connect(self) -> 'Database':
        """
        Stabilisce connessione al database.

        Returns:
            Database: Self per method chaining
        """
        log("🔌 Connessione a MySQL…")
        self._connection = mysql.connector.connect(
            host=self.config.db_host,
            user=self.config.db_user,
            password=self.config.db_pass,
            database=self.config.db_name
        )
        self._cursor = self._connection.cursor()
        return self

    def close(self) -> None:
        """Chiude connessione e cursore."""
        if self._cursor:
            self._cursor.close()
        if self._connection:
            self._connection.close()
        log("🔌 Connessione MySQL chiusa")

    @property
    def cursor(self) -> MySQLCursor:
        """Restituisce il cursore attivo."""
        if not self._cursor:
            raise RuntimeError("Database non connesso. Chiamare connect() prima.")
        return self._cursor

    @property
    def connection(self) -> MySQLConnection:
        """Restituisce la connessione attiva."""
        if not self._connection:
            raise RuntimeError("Database non connesso. Chiamare connect() prima.")
        return self._connection

    def commit(self) -> None:
        """Commit della transazione corrente."""
        self.connection.commit()

    def __enter__(self) -> 'Database':
        """Context manager entry."""
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Context manager exit."""
        self.close()

    # --- Metodi per sync prodotti ---

    def init_sync_tables(self) -> None:
        """Crea tabelle per sincronizzazione prodotti."""
        self.cursor.execute(self.DDL_ONLINE_PRODUCTS)
        self.cursor.execute(self.DDL_PRICE_HISTORY)
        # Migrazione: aggiunge colonne nuove se non esistono
        self._add_column_if_not_exists("Stock_Magazzino", "INT DEFAULT NULL", "Inventory_Item_ID")
        self.commit()

    def _add_column_if_not_exists(self, column_name: str, column_def: str, after_column: str) -> None:
        """
        Aggiunge una colonna alla tabella online_products se non esiste.
        Compatibile con tutte le versioni MySQL.

        Args:
            column_name: Nome della colonna da aggiungere
            column_def: Definizione della colonna (tipo, default, etc.)
            after_column: Colonna dopo cui inserire (per ordine)
        """
        # Verifica se la colonna esiste già
        self.cursor.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
            AND TABLE_NAME = 'online_products'
            AND COLUMN_NAME = %s
        """, (column_name,))
        exists = self.cursor.fetchone()[0] > 0

        if not exists:
            log(f"📊 Migrazione: aggiunta colonna {column_name}...")
            self.cursor.execute(f"""
                ALTER TABLE online_products
                ADD COLUMN {column_name} {column_def} AFTER {after_column}
            """)
            log(f"✅ Colonna {column_name} aggiunta con successo")

    def get_existing_variant_ids(self) -> Set[int]:
        """
        Recupera tutti gli ID varianti esistenti.

        Returns:
            Set[int]: Set di Variant_id
        """
        self.cursor.execute("SELECT Variant_id FROM online_products")
        return {row[0] for row in self.cursor.fetchall()}

    def get_variant_prices(self, variant_id: int) -> Optional[Tuple[Decimal, Decimal]]:
        """
        Recupera prezzi correnti di una variante.

        Args:
            variant_id: ID variante

        Returns:
            Optional[Tuple]: (Price, Compare_AT_Price) o None
        """
        self.cursor.execute(
            "SELECT Price, Compare_AT_Price FROM online_products WHERE Variant_id=%s",
            (variant_id,)
        )
        return self.cursor.fetchone()

    def insert_price_history(
        self,
        variant_id: int,
        old_price: Decimal,
        new_price: Decimal,
        old_compare: Decimal,
        new_compare: Decimal
    ) -> None:
        """Inserisce record nello storico prezzi."""
        self.cursor.execute(
            """INSERT INTO price_history
               (Variant_id, Old_Price, New_Price, Old_Compare_AT, New_Compare_AT)
               VALUES (%s, %s, %s, %s, %s)""",
            (variant_id, old_price, new_price, old_compare, new_compare)
        )

    def upsert_product(
        self,
        variant_id: int,
        variant_title: str,
        sku: str,
        barcode: str,
        product_id: int,
        product_title: str,
        product_handle: str,
        vendor: str,
        price: Decimal,
        compare_at_price: Decimal,
        inventory_item_id: int,
        stock_magazzino: Optional[int],
        tags: str,
        collections: str
    ) -> None:
        """Inserisce o aggiorna prodotto."""
        self.cursor.execute("""
            INSERT INTO online_products (
                Variant_id, Variant_Title, SKU, Barcode,
                Product_id, Product_title, Product_handle, Vendor,
                Price, Compare_AT_Price, Inventory_Item_ID,
                Stock_Magazzino, Tags, Collections
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                Variant_Title=VALUES(Variant_Title),
                SKU=VALUES(SKU),
                Barcode=VALUES(Barcode),
                Product_id=VALUES(Product_id),
                Product_title=VALUES(Product_title),
                Product_handle=VALUES(Product_handle),
                Vendor=VALUES(Vendor),
                Price=VALUES(Price),
                Compare_AT_Price=VALUES(Compare_AT_Price),
                Inventory_Item_ID=VALUES(Inventory_Item_ID),
                Stock_Magazzino=VALUES(Stock_Magazzino),
                Tags=VALUES(Tags),
                Collections=VALUES(Collections)
        """, (
            variant_id, variant_title, sku, barcode,
            product_id, product_title, product_handle, vendor,
            price, compare_at_price, inventory_item_id,
            stock_magazzino, tags, collections
        ))

    def delete_variants(self, variant_ids: Set[int]) -> int:
        """
        Elimina varianti non più presenti.

        Args:
            variant_ids: Set di ID da eliminare

        Returns:
            int: Numero righe eliminate
        """
        if not variant_ids:
            return 0

        placeholders = ",".join(["%s"] * len(variant_ids))
        self.cursor.execute(
            f"DELETE FROM online_products WHERE Variant_id IN ({placeholders})",
            tuple(variant_ids)
        )
        return self.cursor.rowcount

    # --- Metodi per reset varianti ---

    def init_backup_tables(self) -> None:
        """Crea e pulisce tabelle temporanee per backup."""
        self.cursor.execute(self.DDL_VARIANT_BACKUP)
        self.cursor.execute("DELETE FROM variant_backup")
        self.cursor.execute(self.DDL_INVENTORY_BACKUP)
        self.cursor.execute("DELETE FROM inventory_backup")
        self.commit()

    def backup_variant(
        self,
        variant_id: int,
        product_id: int,
        inventory_item_id: Optional[int],
        variant_json: str,
        position: int
    ) -> None:
        """Salva backup di una variante."""
        self.cursor.execute(
            """INSERT INTO variant_backup
               (id, product_id, inventory_item_id, variant_json, position)
               VALUES (%s, %s, %s, %s, %s)""",
            (variant_id, product_id, inventory_item_id, variant_json, position)
        )

    def backup_inventory(
        self,
        variant_id: int,
        inventory_item_id: int,
        location_id: int,
        available: int
    ) -> None:
        """Salva backup di un inventory level."""
        self.cursor.execute(
            """INSERT INTO inventory_backup
               (variant_id, inventory_item_id, location_id, available)
               VALUES (%s, %s, %s, %s)""",
            (variant_id, inventory_item_id, location_id, available)
        )

    def get_variant_backups(
        self,
        product_id: int
    ) -> List[Tuple[int, Optional[int], str, int]]:
        """
        Recupera backup varianti per un prodotto.

        Args:
            product_id: ID prodotto

        Returns:
            List[Tuple]: [(id, inventory_item_id, variant_json, position), ...]
        """
        self.cursor.execute("""
            SELECT id, inventory_item_id, variant_json, position
            FROM variant_backup
            WHERE product_id = %s
            ORDER BY position
        """, (product_id,))
        return self.cursor.fetchall()

    def get_inventory_backups(
        self,
        product_id: int
    ) -> List[Tuple[int, int, int]]:
        """
        Recupera backup inventory per varianti di un prodotto.

        Args:
            product_id: ID prodotto

        Returns:
            List[Tuple]: [(variant_id, location_id, available), ...]
        """
        self.cursor.execute("""
            SELECT variant_id, location_id, available
            FROM inventory_backup
            WHERE variant_id IN (SELECT id FROM variant_backup WHERE product_id = %s)
        """, (product_id,))
        return self.cursor.fetchall()

    def get_original_locations(self, variant_id: int) -> Set[int]:
        """
        Recupera location originali per una variante.

        Args:
            variant_id: ID variante originale

        Returns:
            Set[int]: Set di location_id
        """
        self.cursor.execute("""
            SELECT DISTINCT location_id
            FROM inventory_backup
            WHERE variant_id = %s
        """, (variant_id,))
        return {row[0] for row in self.cursor.fetchall()}
