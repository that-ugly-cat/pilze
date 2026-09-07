"""Test degli account. Nato da un bug vero: `INSERT INTO users VALUES (?,?,?,?,?)` era
posizionale, e la prima migrazione che ha aggiunto una colonna ha rotto la creazione di
account in produzione — silenziosamente, perché nessuno crea utenti tutti i giorni.
"""

import pytest


def test_creazione_utente_dopo_una_migrazione(tmp_path):
    """Il caso che si era rotto: tabella migrata, poi si crea un account."""
    from webapp import auth
    p = tmp_path / "users.db"
    auth.create_user("primo", "password-uno", is_admin=True, db_path=p)
    assert auth.migrate(p) == ["home_lat", "home_lon"]
    # dopo la migrazione la tabella ha due colonne in più: l'insert deve reggere lo stesso
    assert auth.create_user("secondo", "password-due", db_path=p) is True
    assert auth.verify("secondo", "password-due", db_path=p)
    assert auth.get_user("secondo", db_path=p)["is_admin"] == 0


def test_utente_duplicato_rifiutato(tmp_path):
    from webapp import auth
    p = tmp_path / "users.db"
    assert auth.create_user("tizio", "password-uno", db_path=p) is True
    assert auth.create_user("tizio", "password-due", db_path=p) is False
    assert auth.verify("tizio", "password-uno", db_path=p)      # la prima resta valida


def test_cambio_password_chiude_le_altre_sessioni(tmp_path):
    from webapp import auth
    p = tmp_path / "users.db"
    auth.create_user("tizio", "vecchia-password", db_path=p)
    mia = auth.open_session("tizio", db_path=p)
    altra = auth.open_session("tizio", db_path=p)
    auth.set_password("tizio", "nuova-password", db_path=p)
    auth.close_other_sessions("tizio", mia, db_path=p)
    assert auth.session_user(mia, db_path=p) is not None        # la mia resta
    assert auth.session_user(altra, db_path=p) is None          # le altre cadono
    assert auth.verify("tizio", "nuova-password", db_path=p)
    assert not auth.verify("tizio", "vecchia-password", db_path=p)


def test_casa_si_imposta_e_si_toglie(tmp_path):
    from webapp import auth
    p = tmp_path / "users.db"
    auth.create_user("tizio", "password-uno", db_path=p)
    auth.migrate(p)
    assert auth.get_user("tizio", db_path=p)["home_lat"] is None
    auth.set_home("tizio", 45.4064, 11.8768, db_path=p)
    assert auth.get_user("tizio", db_path=p)["home_lat"] == pytest.approx(45.4064)
    auth.set_home("tizio", None, None, db_path=p)
    assert auth.get_user("tizio", db_path=p)["home_lon"] is None
