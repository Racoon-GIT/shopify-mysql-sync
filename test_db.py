"""
Test per la configurazione TLS della connessione MySQL (rollout handoff-13,
passo shopify-sync-ws — IT/SVILUPPO/docs/tls-rollout-handoff13-2026-06-28.md).

`build_ssl_config()` è una funzione pura sui parametri: nessun mock di
mysql.connector necessario per quella parte (TestBuildSslConfig*). Il blocco
`TestConnectWiring` verifica invece che il valore prodotto arrivi davvero a
`mysql.connector.connect` — è il test di mutazione: se il ramo
`**build_ssl_config()` viene tolto da `Database.connect()`, questo test
diventa rosso (verificato a mano, vedi report di consegna).
"""

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from src.config import Config
from mysql.connector.errors import InterfaceError

from src.db import Database, build_ssl_config


FAKE_PEM = "-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n"


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


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """DB_CA_CERT non deve mai trapelare da un test all'altro."""
    monkeypatch.delenv("DB_CA_CERT", raising=False)


class TestBuildSslConfigWithCa:
    """DB_CA_CERT impostata: catena verificata, nessun downgrade."""

    def test_valid_ca_produces_verified_chain_params(self, monkeypatch):
        b64 = base64.b64encode(FAKE_PEM.encode("utf-8")).decode("ascii")
        monkeypatch.setenv("DB_CA_CERT", b64)

        ssl_config = build_ssl_config()

        assert ssl_config["ssl_verify_cert"] is True
        assert ssl_config["ssl_verify_identity"] is True
        assert "ssl_ca" in ssl_config

        ca_path = ssl_config["ssl_ca"]
        try:
            with open(ca_path, "rb") as f:
                assert f.read() == FAKE_PEM.encode("utf-8")
        finally:
            os.unlink(ca_path)

    def test_valid_ca_does_not_log_the_fallback_warning(self, monkeypatch, capsys):
        b64 = base64.b64encode(FAKE_PEM.encode("utf-8")).decode("ascii")
        monkeypatch.setenv("DB_CA_CERT", b64)

        ssl_config = build_ssl_config()
        os.unlink(ssl_config["ssl_ca"])

        captured = capsys.readouterr()
        assert "DB_CA_CERT non impostata" not in captured.out

    def test_invalid_base64_fails_loud_not_silent_fallback(self, monkeypatch):
        """Un valore presente ma corrotto è un errore di config, non un
        motivo per ricadere silenziosamente in encrypt-only."""
        monkeypatch.setenv("DB_CA_CERT", "questo non e' base64 valido !!! ###")

        with pytest.raises(RuntimeError, match="DB_CA_CERT"):
            build_ssl_config()


class TestBuildSslConfigWithoutCa:
    """DB_CA_CERT assente: fallback encrypt-only, rumoroso non silenzioso."""

    def test_missing_env_var_falls_back_to_encrypt_only(self):
        ssl_config = build_ssl_config()

        assert ssl_config == {
            "ssl_verify_cert": False,
            "ssl_verify_identity": False,
        }
        assert "ssl_ca" not in ssl_config

    def test_missing_env_var_logs_loudly(self, capsys):
        build_ssl_config()

        captured = capsys.readouterr()
        assert "DB_CA_CERT" in captured.out


class TestConnectWiring:
    """Prova di mutazione: la config TLS deve raggiungere mysql.connector.connect.

    Verificato a mano (non solo asserito): commentando `**build_ssl_config()`
    in `Database.connect()`, `test_connect_passes_ssl_params_to_connector`
    diventa rosso con KeyError su `ssl_verify_cert` — vedi report.
    """

    def test_connect_passes_ssl_params_to_connector(self, monkeypatch):
        b64 = base64.b64encode(FAKE_PEM.encode("utf-8")).decode("ascii")
        monkeypatch.setenv("DB_CA_CERT", b64)

        with patch("src.db.mysql.connector.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            db = Database(_make_config())
            db.connect()

        _, kwargs = mock_connect.call_args
        assert kwargs["ssl_verify_cert"] is True
        assert kwargs["ssl_verify_identity"] is True
        assert "ssl_ca" in kwargs
        os.unlink(kwargs["ssl_ca"])

    def test_connect_encrypt_only_wiring_without_ca(self, monkeypatch):
        with patch("src.db.mysql.connector.connect") as mock_connect:
            mock_connect.return_value = MagicMock()
            db = Database(_make_config())
            db.connect()

        _, kwargs = mock_connect.call_args
        assert kwargs["ssl_verify_cert"] is False
        assert kwargs["ssl_verify_identity"] is False
        assert "ssl_ca" not in kwargs


class TestConnectFailureDiagnostics:
    """
    I due controlli TLS attivi (catena + identità) falliscono con lo STESSO
    testo del driver, quindi connect() aggiunge una riga che nomina entrambe
    le cause. Il test che conta è il secondo: senza il controllo negativo, una
    riga stampata SEMPRE passerebbe il primo test e non proverebbe nulla.
    """

    def _failing_connect(self, monkeypatch, capsys, exc):
        b64 = base64.b64encode(FAKE_PEM.encode("utf-8")).decode("ascii")
        monkeypatch.setenv("DB_CA_CERT", b64)
        with patch("src.db.mysql.connector.connect", side_effect=exc):
            db = Database(_make_config())
            with pytest.raises(type(exc)):
                db.connect()
        return capsys.readouterr().out

    def test_certificate_failure_names_both_causes_and_the_discriminator(self, monkeypatch, capsys):
        out = self._failing_connect(
            monkeypatch, capsys,
            InterfaceError("2026 (HY000): SSL connection error: error:0A000086:SSL "
                           "routines::certificate verify failed"),
        )

        assert "CATENA" in out and "IDENTITÀ" in out
        assert "ssl_verify_identity=False" in out
        assert "db.example.com" in out

    def test_unrelated_failure_gets_no_tls_hint(self, monkeypatch, capsys):
        out = self._failing_connect(
            monkeypatch, capsys,
            InterfaceError("1045 (28000): Access denied for user 'u'@'host'"),
        )

        assert "ssl_verify_identity=False" not in out
        assert "CATENA" not in out

    def test_diagnostic_never_logs_the_password(self, monkeypatch, capsys):
        out = self._failing_connect(
            monkeypatch, capsys,
            InterfaceError("SSL connection error: certificate verify failed"),
        )

        assert "db_pass" not in out
        # `p` è la password della config di test: si controlla la forma in cui
        # finirebbe nel log (password='p'), non la lettera isolata.
        assert "password" not in out.lower()


class TestBuildSslConfigHardening:
    """
    Rami aggiunti in revisione (2026-08-15): un DB_CA_CERT base64-valido ma che
    non è un certificato, e il riuso del file temporaneo fra chiamate.
    Entrambi allineano questo consumer al gemello feed-server.
    """

    def test_valid_base64_but_not_a_certificate_fails_loudly(self, monkeypatch):
        # "AAAA" è base64 legittimo (decodifica in 3 byte nulli): senza il
        # controllo sul marcatore PEM passerebbe per una CA buona, e l'errore
        # arriverebbe molto dopo, dal driver TLS, senza nominare la causa.
        monkeypatch.setattr("src.db._ca_file_path", None, raising=False)
        monkeypatch.setenv("DB_CA_CERT", "AAAA")

        with pytest.raises(RuntimeError) as exc:
            build_ssl_config()

        assert "BEGIN CERTIFICATE" in str(exc.value)
        assert "DB_CA_CERT" in str(exc.value)

    def test_temp_ca_file_is_reused_across_calls(self, monkeypatch):
        # Senza cache ogni connect() lascerebbe un .pem in più in /tmp.
        monkeypatch.setattr("src.db._ca_file_path", None, raising=False)
        monkeypatch.setenv("DB_CA_CERT", base64.b64encode(FAKE_PEM.encode()).decode())

        first = build_ssl_config()
        second = build_ssl_config()

        assert first["ssl_ca"] == second["ssl_ca"]
        assert os.path.exists(first["ssl_ca"])
        with open(first["ssl_ca"], "rb") as fh:
            assert b"BEGIN CERTIFICATE" in fh.read()

        # Identità verificata sia sul primo decode sia sulla chiamata
        # successiva che serve dalla cache: sono due `return` diversi in
        # build_ssl_config() e devono restare allineati.
        assert first["ssl_verify_identity"] is True
        assert second["ssl_verify_identity"] is True

    def test_cache_is_rebuilt_if_the_temp_file_disappears(self, monkeypatch):
        # /tmp può essere ripulito sotto i piedi del processo: la cache non deve
        # restituire un path morto, che il driver rifiuterebbe all'handshake.
        monkeypatch.setattr("src.db._ca_file_path", None, raising=False)
        monkeypatch.setenv("DB_CA_CERT", base64.b64encode(FAKE_PEM.encode()).decode())

        first = build_ssl_config()
        os.unlink(first["ssl_ca"])
        second = build_ssl_config()

        assert second["ssl_ca"] != first["ssl_ca"]
        assert os.path.exists(second["ssl_ca"])
