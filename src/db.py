# src/db.py
"""
Gestione database MySQL centralizzata.
"""

import base64
import binascii
import os
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Tuple, Any, Set, Dict, Iterable
from decimal import Decimal
from zoneinfo import ZoneInfo
import mysql.connector
from mysql.connector import MySQLConnection
from mysql.connector.cursor import MySQLCursor

from .config import Config, log


# Timezone di riferimento per lo schedule assunto del sync giornaliero
# (lag-check, vedi Database.get_last_sync_run / _assumed_last_sync_utc).
_ROME_TZ = ZoneInfo("Europe/Rome")


def _assumed_last_sync_utc(now_utc: Optional[datetime] = None) -> datetime:
    """
    Schedule ASSUNTO del sync giornaliero: la 03:04 Europe/Rome più recente non
    successiva a `now_utc` (03:00 di cron + ~4 minuti di durata media del run).
    Fallback usato da `Database.get_last_sync_run()` quando `scheduler_job_logs`
    non è leggibile o non ha righe per il job — mai un'eccezione, sempre un valore.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    now_rome = now_utc.astimezone(_ROME_TZ)
    candidate = now_rome.replace(hour=3, minute=4, second=0, microsecond=0)
    if candidate > now_rome:
        candidate -= timedelta(days=1)
    return candidate.astimezone(timezone.utc)


# Path del file temporaneo con la CA decodificata, riusato per tutto il processo
# (vedi build_ssl_config): senza, ogni connect() ne lascerebbe uno nuovo in /tmp.
_ca_file_path: Optional[str] = None


def build_ssl_config() -> Dict[str, object]:
    """
    Costruisce i parametri TLS per mysql-connector-python verso `racoon`
    (Hetzner, mysqld 8.0.45) — rollout handoff-13
    (../_archive/tls-rollout-handoff13-2026-06-28.md), riga shopify-sync-ws.

    Dal 2026-08-16 il server presenta un certificato emesso dalla `Racoon
    Internal CA` propria (non più il self-signed auto-generato da MySQL),
    con SAN = IP:78.46.244.227, DNS:racoon-db-vps1. Con la CA disponibile
    la verifica dell'HOSTNAME è quindi attiva (`ssl_verify_identity=True`)
    oltre a quella della CATENA (`ssl_verify_cert=True`): la sessione
    SERVER l'ha misurato contro la produzione con
    mysql-connector-python==9.6.0 (versione pinnata) — connessione per IP
    con `ssl_verify_identity=True` riesce perché l'IP è nel SAN, mentre lo
    stesso server con un nome assente dal SAN fallisce. La connessione
    resta per IP: host, CA e DNS non cambiano.

    Attenzione a non generalizzare per analogia: il driver Node `mysql2`
    ha un difetto di plumbing per cui, connettendosi per IP, la verifica
    dell'identità non è utilizzabile (passa a `tls.connect` un socket già
    connesso con `servername undefined`, e Node finisce per confrontare
    contro `localhost`) — questa configurazione non è quindi trasferibile
    ai consumer Node di `racoon`. Riferimento gemello già in produzione:
    `Feed-Exporter/src/mysql_client.py` (commit 77f6e86, live dal
    2026-08-18).

    Senza CA (DB_CA_CERT non impostata) resta il fallback encrypt-only:
    `ssl_verify_cert=False` e `ssl_verify_identity=False` — verificare
    l'identità senza verificare la catena non protegge da niente,
    chiunque potrebbe presentare un certificato col nome giusto se
    nessuno controlla chi l'ha firmato.

    Render non ha filesystem persistente: la CA arriva come base64 su una
    riga nella env var DB_CA_CERT, decodificata a runtime in un file
    temporaneo (mysql-connector-python richiede un path per ssl_ca, non
    accetta bytes in memoria).
    """
    ca_b64 = os.environ.get("DB_CA_CERT")

    if not ca_b64:
        # DB_CA_CERT non impostata: fallback esplicito e rumoroso, NON un
        # downgrade silenzioso. Scelta coerente con lo stesso rollout su
        # scheduler-app (Scheduler/src/lib/db.ts::buildSsl) e con il
        # trade-off già accettato nel documento per stock-check/
        # price-bulk-updt (§Security level by platform: "encrypt-only...
        # Acceptable given a controlled egress + 3306 firewall hardening").
        # Un fail-hard qui fermerebbe l'unico sync giornaliero di
        # online_products senza un guadagno di sicurezza proporzionato:
        # OGGI, senza questa modifica, il consumer gira SENZA alcun TLS —
        # encrypt-only è quindi già un miglioramento netto, non un
        # downgrade. Se in futuro serve una postura più stretta
        # specificamente per questo consumer (è il WRITER di
        # online_products, letto da più progetti a valle), è una decisione
        # di business da prendere a monte (Ale/SVILUPPO), non da introdurre
        # qui in modo silenzioso/unilaterale.
        log(
            "⚠️ DB_CA_CERT non impostata: connessione a racoon in TLS "
            "encrypt-only (nessuna verifica della CA). Impostare DB_CA_CERT "
            "su Render per la verifica completa della catena — vedi "
            "_archive/tls-rollout-handoff13-2026-06-28.md."
        )
        return {
            "ssl_verify_cert": False,
            "ssl_verify_identity": False,
        }

    # File temporaneo riusato per tutto il processo: `connect()` viene chiamata
    # più volte (sync giornaliero + reset manuale) e senza cache ogni chiamata
    # lascerebbe un .pem in più in /tmp, mai cancellato. Stesso pattern del
    # consumer gemello feed-server (`Feed-Exporter/src/mysql_client.py`).
    global _ca_file_path
    if _ca_file_path and os.path.exists(_ca_file_path):
        return {
            "ssl_ca": _ca_file_path,
            "ssl_verify_cert": True,
            "ssl_verify_identity": True,
        }

    try:
        ca_pem = base64.b64decode(ca_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        # A differenza della env var assente (stato transitorio legittimo
        # durante il rollout), un valore presente ma non decodificabile è
        # quasi certamente un errore di configurazione (copia-incolla
        # troncato/corrotto): qui falliamo forte, non silenziosamente in
        # encrypt-only — nascondere un errore di configurazione dietro un
        # fallback "silenziosamente funzionante" sarebbe la trappola
        # peggiore da evitare per chi imposta la variabile credendo di
        # aver attivato la verifica della CA.
        raise RuntimeError(
            f"DB_CA_CERT presente ma non è base64 valido: {exc}"
        ) from exc

    # Base64 valido non significa certificato: un valore troncato a metà
    # decodifica senza errori. Il marcatore PEM è il controllo che fa dire
    # all'errore cosa guardare, invece di lasciarlo emergere dal driver TLS.
    if b"BEGIN CERTIFICATE" not in ca_pem:
        raise RuntimeError(
            f"DB_CA_CERT decodificata ({len(ca_pem)} byte) non contiene un "
            'certificato PEM ("BEGIN CERTIFICATE" assente): valore troncato o '
            "file sbagliato. Reimpostare la env var con la CA del server racoon "
            "codificata in base64 su una riga sola."
        )

    ca_file = tempfile.NamedTemporaryFile(
        mode="wb", suffix=".pem", prefix="db-ca-", delete=False
    )
    try:
        ca_file.write(ca_pem)
    finally:
        ca_file.close()

    _ca_file_path = ca_file.name

    return {
        "ssl_ca": ca_file.name,
        "ssl_verify_cert": True,
        "ssl_verify_identity": True,
    }


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
        Product_Type      VARCHAR(255) DEFAULT NULL,
        Price             DECIMAL(10,2),
        Compare_AT_Price  DECIMAL(10,2),
        Inventory_Item_ID BIGINT,
        Stock_Magazzino   INT DEFAULT NULL,
        Tags              TEXT,
        Collections       TEXT,
        -- Nuovi campi: Body HTML
        Body_HTML         LONGTEXT DEFAULT NULL,
        -- Nuovi campi: Immagini (JSON)
        Product_Images    JSON DEFAULT NULL,
        -- Metafield Prodotto
        MF_Customization_Description TEXT DEFAULT NULL,
        MF_Shoe_Details              TEXT DEFAULT NULL,
        MF_Customization_Details     TEXT DEFAULT NULL,
        MF_O_Description             TEXT DEFAULT NULL,
        MF_Handling                  INT DEFAULT NULL,
        MF_Google_Custom_Product     BOOLEAN DEFAULT NULL,
        -- Metafield Google Shopping (a livello prodotto, applicati a tutte le varianti)
        MF_Google_Age_Group      VARCHAR(100) DEFAULT NULL,
        MF_Google_Condition      VARCHAR(100) DEFAULT NULL,
        MF_Google_Gender         VARCHAR(100) DEFAULT NULL,
        MF_Google_MPN            VARCHAR(255) DEFAULT NULL,
        MF_Google_Custom_Label_0 VARCHAR(255) DEFAULT NULL,
        MF_Google_Custom_Label_1 VARCHAR(255) DEFAULT NULL,
        MF_Google_Custom_Label_2 VARCHAR(255) DEFAULT NULL,
        MF_Google_Custom_Label_3 VARCHAR(255) DEFAULT NULL,
        MF_Google_Custom_Label_4 VARCHAR(255) DEFAULT NULL,
        MF_Google_Size_System    VARCHAR(100) DEFAULT NULL,
        MF_Google_Size_Type      VARCHAR(100) DEFAULT NULL,
        -- Campi Google Merchant Center
        MF_Google_Color          VARCHAR(255) DEFAULT NULL,
        MF_Google_Size           VARCHAR(100) DEFAULT NULL,
        MF_Google_Material       VARCHAR(255) DEFAULT NULL,
        MF_Google_Product_Category VARCHAR(500) DEFAULT NULL
    )
    """

    # Lista colonne da migrare (nome, tipo, colonna precedente)
    MIGRATION_COLUMNS = [
        ("Product_Type", "VARCHAR(255) DEFAULT NULL", "Vendor"),
        ("Stock_Magazzino", "INT DEFAULT NULL", "Inventory_Item_ID"),
        ("Body_HTML", "LONGTEXT DEFAULT NULL", "Collections"),
        ("Product_Images", "JSON", "Body_HTML"),
        ("MF_Customization_Description", "TEXT DEFAULT NULL", "Product_Images"),
        ("MF_Shoe_Details", "TEXT DEFAULT NULL", "MF_Customization_Description"),
        ("MF_Customization_Details", "TEXT DEFAULT NULL", "MF_Shoe_Details"),
        ("MF_O_Description", "TEXT DEFAULT NULL", "MF_Customization_Details"),
        ("MF_Handling", "INT DEFAULT NULL", "MF_O_Description"),
        ("MF_Google_Custom_Product", "BOOLEAN DEFAULT NULL", "MF_Handling"),
        ("MF_Google_Age_Group", "VARCHAR(100) DEFAULT NULL", "MF_Google_Custom_Product"),
        ("MF_Google_Condition", "VARCHAR(100) DEFAULT NULL", "MF_Google_Age_Group"),
        ("MF_Google_Gender", "VARCHAR(100) DEFAULT NULL", "MF_Google_Condition"),
        ("MF_Google_MPN", "VARCHAR(255) DEFAULT NULL", "MF_Google_Gender"),
        ("MF_Google_Custom_Label_0", "VARCHAR(255) DEFAULT NULL", "MF_Google_MPN"),
        ("MF_Google_Custom_Label_1", "VARCHAR(255) DEFAULT NULL", "MF_Google_Custom_Label_0"),
        ("MF_Google_Custom_Label_2", "VARCHAR(255) DEFAULT NULL", "MF_Google_Custom_Label_1"),
        ("MF_Google_Custom_Label_3", "VARCHAR(255) DEFAULT NULL", "MF_Google_Custom_Label_2"),
        ("MF_Google_Custom_Label_4", "VARCHAR(255) DEFAULT NULL", "MF_Google_Custom_Label_3"),
        ("MF_Google_Size_System", "VARCHAR(100) DEFAULT NULL", "MF_Google_Custom_Label_4"),
        ("MF_Google_Size_Type", "VARCHAR(100) DEFAULT NULL", "MF_Google_Size_System"),
        # Campi Google Merchant Center
        ("MF_Google_Color", "VARCHAR(255) DEFAULT NULL", "MF_Google_Size_Type"),
        ("MF_Google_Size", "VARCHAR(100) DEFAULT NULL", "MF_Google_Color"),
        ("MF_Google_Material", "VARCHAR(255) DEFAULT NULL", "MF_Google_Size"),
        ("MF_Google_Product_Category", "VARCHAR(500) DEFAULT NULL", "MF_Google_Material"),
    ]

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

    # --- Costanti lag-check (sola lettura, vedi metodi a fine classe) ---
    # Nome del job Scheduler da cercare in racoon.scheduler_jobs.
    SCHEDULER_JOB_NAME = "Shopify-MySQL-Sync"
    # Righe per statement in get_mirror_rows_by_variant_ids (un placeholder per id).
    MIRROR_CHUNK_SIZE = 500

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
        try:
            self._connection = mysql.connector.connect(
                host=self.config.db_host,
                user=self.config.db_user,
                password=self.config.db_pass,
                database=self.config.db_name,
                **build_ssl_config()
            )
        except Exception as exc:
            # Da quando questo consumer verifica anche l'IDENTITÀ dell'host
            # (2026-08-18), i due controlli TLS falliscono con lo STESSO testo
            # generico del driver: `certificate verify failed` non dice se ha
            # ceduto la catena o l'identità (misurato dalla sessione SERVER
            # contro la produzione con mysql-connector-python 9.6.0; su Node
            # `mysql2` i due casi hanno invece codici distinti). Questo job
            # gira di notte senza nessuno che guardi: il log è l'unica traccia
            # che resta, quindi nomina le due cause e come separarle.
            # L'eccezione NON cambia: chi chiama vede l'errore del driver.
            if "certificate verify failed" in str(exc).lower():
                log(
                    f"   ↳ TLS: verifica rifiutata verso l'host {self.config.db_host!r}. "
                    "Due cause possibili: (1) CATENA — DB_CA_CERT non è la CA che ha "
                    "firmato il certificato del server (CA ruotata lato server, o env var "
                    "con una CA diversa; un valore troncato verrebbe intercettato prima, "
                    "in build_ssl_config); (2) IDENTITÀ — ssl_verify_identity=True e "
                    "l'host qui sopra non è nel SAN del certificato (host cambiato, o "
                    "certificato riemesso senza). Per distinguerle: ritentare la stessa "
                    "connessione con ssl_verify_identity=False — se passa, la catena è "
                    "sana e la causa è la (2)."
                )
            raise
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
        # Migrazione: aggiunge tutte le colonne nuove se non esistono
        for col_name, col_def, after_col in self.MIGRATION_COLUMNS:
            self._add_column_if_not_exists(col_name, col_def, after_col)
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
        product_type: Optional[str],
        price: Decimal,
        compare_at_price: Decimal,
        inventory_item_id: int,
        stock_magazzino: Optional[int],
        tags: str,
        collections: str,
        # Nuovi campi
        body_html: Optional[str] = None,
        product_images: Optional[str] = None,  # JSON string
        # Metafield Prodotto
        mf_customization_description: Optional[str] = None,
        mf_shoe_details: Optional[str] = None,
        mf_customization_details: Optional[str] = None,
        mf_o_description: Optional[str] = None,
        mf_handling: Optional[int] = None,
        mf_google_custom_product: Optional[bool] = None,
        # Metafield Google Shopping (da prodotto)
        mf_google_age_group: Optional[str] = None,
        mf_google_condition: Optional[str] = None,
        mf_google_gender: Optional[str] = None,
        mf_google_mpn: Optional[str] = None,
        mf_google_custom_label_0: Optional[str] = None,
        mf_google_custom_label_1: Optional[str] = None,
        mf_google_custom_label_2: Optional[str] = None,
        mf_google_custom_label_3: Optional[str] = None,
        mf_google_custom_label_4: Optional[str] = None,
        mf_google_size_system: Optional[str] = None,
        mf_google_size_type: Optional[str] = None,
        # Campi Google Merchant Center
        mf_google_color: Optional[str] = None,
        mf_google_size: Optional[str] = None,
        mf_google_material: Optional[str] = None,
        mf_google_product_category: Optional[str] = None,
    ) -> None:
        """Inserisce o aggiorna prodotto."""
        self.cursor.execute("""
            INSERT INTO online_products (
                Variant_id, Variant_Title, SKU, Barcode,
                Product_id, Product_title, Product_handle, Vendor,
                Product_Type, Price, Compare_AT_Price, Inventory_Item_ID,
                Stock_Magazzino, Tags, Collections,
                Body_HTML, Product_Images,
                MF_Customization_Description, MF_Shoe_Details,
                MF_Customization_Details, MF_O_Description,
                MF_Handling, MF_Google_Custom_Product,
                MF_Google_Age_Group, MF_Google_Condition,
                MF_Google_Gender, MF_Google_MPN,
                MF_Google_Custom_Label_0, MF_Google_Custom_Label_1,
                MF_Google_Custom_Label_2, MF_Google_Custom_Label_3,
                MF_Google_Custom_Label_4, MF_Google_Size_System,
                MF_Google_Size_Type, MF_Google_Color,
                MF_Google_Size, MF_Google_Material,
                MF_Google_Product_Category
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                Variant_Title=VALUES(Variant_Title),
                SKU=VALUES(SKU),
                Barcode=VALUES(Barcode),
                Product_id=VALUES(Product_id),
                Product_title=VALUES(Product_title),
                Product_handle=VALUES(Product_handle),
                Vendor=VALUES(Vendor),
                Product_Type=VALUES(Product_Type),
                Price=VALUES(Price),
                Compare_AT_Price=VALUES(Compare_AT_Price),
                Inventory_Item_ID=VALUES(Inventory_Item_ID),
                Stock_Magazzino=VALUES(Stock_Magazzino),
                Tags=VALUES(Tags),
                Collections=VALUES(Collections),
                Body_HTML=VALUES(Body_HTML),
                Product_Images=VALUES(Product_Images),
                MF_Customization_Description=VALUES(MF_Customization_Description),
                MF_Shoe_Details=VALUES(MF_Shoe_Details),
                MF_Customization_Details=VALUES(MF_Customization_Details),
                MF_O_Description=VALUES(MF_O_Description),
                MF_Handling=VALUES(MF_Handling),
                MF_Google_Custom_Product=VALUES(MF_Google_Custom_Product),
                MF_Google_Age_Group=VALUES(MF_Google_Age_Group),
                MF_Google_Condition=VALUES(MF_Google_Condition),
                MF_Google_Gender=VALUES(MF_Google_Gender),
                MF_Google_MPN=VALUES(MF_Google_MPN),
                MF_Google_Custom_Label_0=VALUES(MF_Google_Custom_Label_0),
                MF_Google_Custom_Label_1=VALUES(MF_Google_Custom_Label_1),
                MF_Google_Custom_Label_2=VALUES(MF_Google_Custom_Label_2),
                MF_Google_Custom_Label_3=VALUES(MF_Google_Custom_Label_3),
                MF_Google_Custom_Label_4=VALUES(MF_Google_Custom_Label_4),
                MF_Google_Size_System=VALUES(MF_Google_Size_System),
                MF_Google_Size_Type=VALUES(MF_Google_Size_Type),
                MF_Google_Color=VALUES(MF_Google_Color),
                MF_Google_Size=VALUES(MF_Google_Size),
                MF_Google_Material=VALUES(MF_Google_Material),
                MF_Google_Product_Category=VALUES(MF_Google_Product_Category)
        """, (
            variant_id, variant_title, sku, barcode,
            product_id, product_title, product_handle, vendor,
            product_type, price, compare_at_price, inventory_item_id,
            stock_magazzino, tags, collections,
            body_html, product_images,
            mf_customization_description, mf_shoe_details,
            mf_customization_details, mf_o_description,
            mf_handling, mf_google_custom_product,
            mf_google_age_group, mf_google_condition,
            mf_google_gender, mf_google_mpn,
            mf_google_custom_label_0, mf_google_custom_label_1,
            mf_google_custom_label_2, mf_google_custom_label_3,
            mf_google_custom_label_4, mf_google_size_system,
            mf_google_size_type, mf_google_color,
            mf_google_size, mf_google_material,
            mf_google_product_category
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

    # --- Metodi per lag-check (sola lettura — nessun INSERT/UPDATE/DELETE/DDL) ---

    def get_mirror_count(self) -> int:
        """
        Conteggio righe in online_products. Sola lettura.

        Returns:
            int: numero di righe in online_products.
        """
        self.cursor.execute("SELECT COUNT(*) FROM online_products")
        row = self.cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0

    def get_mirror_rows_by_variant_ids(
        self, variant_ids: Iterable[int]
    ) -> Dict[int, Dict[str, Any]]:
        """
        Righe mirror per un insieme di Variant_id, in chunk da `MIRROR_CHUNK_SIZE`
        (un parametro per id) per restare sotto i limiti di placeholder del driver.
        Sola lettura.

        Args:
            variant_ids: Variant_id Shopify da cercare nel mirror.

        Returns:
            Dict[int, Dict]: variant_id -> {sku, variant_title, price,
            inventory_item_id}. Le varianti assenti dal mirror non compaiono.
        """
        ids = [int(v) for v in variant_ids]
        result: Dict[int, Dict[str, Any]] = {}
        for i in range(0, len(ids), self.MIRROR_CHUNK_SIZE):
            chunk = ids[i:i + self.MIRROR_CHUNK_SIZE]
            placeholders = ",".join(["%s"] * len(chunk))
            self.cursor.execute(
                f"""
                SELECT Variant_id, SKU, Variant_Title, Price, Inventory_Item_ID
                FROM online_products
                WHERE Variant_id IN ({placeholders})
                """,
                tuple(chunk),
            )
            for variant_id, sku, variant_title, price, inventory_item_id in self.cursor.fetchall():
                result[variant_id] = {
                    "sku": sku,
                    "variant_title": variant_title,
                    "price": price,
                    "inventory_item_id": inventory_item_id,
                }
        return result

    def get_real_stock(self, sku: str, size: str) -> Optional[int]:
        """
        Stock reale per (sku lato ordine, taglia) — stessa espressione usata da
        `SP_SHOP_ORDER_IN` (verificata live su racoon, 2026-09-22; vedi
        `docs/sync-lag-plan.md` — non ridisegnare). `sku_root` ha PK composita
        (SKU, SKU_ROOT): uno SKU lato ordine può mappare più SKU_ROOT, e la SUM
        li somma tutti — corretto, non un bug. Tocca solo `stock` e `sku_root`,
        sola lettura.

        Args:
            sku: SKU lato ordine (es. `online_products.SKU` / `log.SKU`).
            size: Taglia (es. `online_products.Variant_Title` / `log.SIZE`).

        Returns:
            Optional[int]: quantità sommata, o None se la query fallisce per
            qualunque motivo (grant mancante, tabella irraggiungibile, ecc.) —
            mai un'eccezione, mai 0 come sostituto silenzioso di "sconosciuto".
        """
        try:
            self.cursor.execute(
                """
                SELECT IFNULL(SUM(s.QTY), 0) AS Qty
                FROM stock s
                WHERE s.RAW_SHOE_SKU IN (
                  SELECT SKU_ROOT FROM sku_root WHERE SKU = %s
                )
                AND s.SIZE = %s
                """,
                (sku, size),
            )
            row = self.cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])
        except Exception as exc:
            log(f"⚠️ get_real_stock({sku!r}, {size!r}) fallita: {exc}")
            try:
                self.connection.rollback()
            except Exception:
                pass
            return None

    def get_last_sync_run(self) -> Tuple[Optional[datetime], str]:
        """
        Ultima esecuzione REGISTRATA del job Scheduler 'Shopify-MySQL-Sync' —
        racoon.scheduler_jobs + racoon.scheduler_job_logs, proprietà di
        `Scheduler`, sola lettura qui. Non solleva mai: qualunque fallimento
        (grant mancante, tabella/riga assente) ricade sullo schedule assunto
        (03:04 Europe/Rome più recente non successiva a ora), sempre disponibile.

        Returns:
            Tuple[datetime | None, str]: (istante UTC, sorgente). Sorgente e'
            "scheduler_job_logs" se osservata, "assumed_schedule" se calcolata.
        """
        try:
            self.cursor.execute(
                """
                SELECT DATE_FORMAT(sjl.executed_at, '%%Y-%%m-%%d %%H:%%i:%%s') AS executed_at_str
                FROM scheduler_job_logs sjl
                JOIN scheduler_jobs sj ON sj.id = sjl.job_id
                WHERE sj.name = %s
                ORDER BY sjl.executed_at DESC
                LIMIT 1
                """,
                (self.SCHEDULER_JOB_NAME,),
            )
            row = self.cursor.fetchone()
            if row and row[0]:
                dt = datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                return dt, "scheduler_job_logs"
        except Exception as exc:
            log(f"⚠️ get_last_sync_run: lettura scheduler_job_logs fallita, uso schedule assunto: {exc}")
            try:
                self.connection.rollback()
            except Exception:
                pass

        return _assumed_last_sync_utc(), "assumed_schedule"
