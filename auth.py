"""Bramka hasła. W repo nie ma ani hasła, ani jego skrótu – tylko w sekretach (Streamlit Secrets / zmienna środowiskowa).

Skrót: scrypt (sól 16 B, n=2^15, r=8, p=1), format `scrypt$n$r$p$sól_b64$skrót_b64`.
Nowy skrót: `python auth.py` (hasło wpisujesz niewidocznie), wynik wklej jako APP_PASSWORD_HASH.
Brak APP_PASSWORD_HASH = aplikacja zablokowana (fail closed), żeby zapomniany sekret nie otwierał danych.
"""
import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import time

import streamlit as st

import login_ui

MAX_ATTEMPTS = 5  # na sesję przeglądarki
WINDOW_S = 15 * 60
IP_MAX_FAILS = 20  # na publiczny adres IP w oknie WINDOW_S – blokuje tylko ten adres, nie wszystkich
SLOWDOWN_FAILS = 30  # tyle błędów na cały serwer w oknie = każda kolejna błędna próba trwa dłużej
DELAY_S, SLOW_DELAY_S = 1.5, 5.0
# Celowo brak globalnej blokady: pozwalałaby każdemu z zewnątrz odciąć wszystkich (także z poprawnym hasłem).
MAXMEM = 64 * 1024 * 1024


def hash_password(password: str, n: int = 2**15, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=MAXMEM, dklen=32)
    return f"scrypt${n}${r}${p}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, dk = stored.strip().split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(dk)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                             maxmem=MAXMEM, dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


def _stored_hash() -> str | None:
    try:
        value = st.secrets.get("APP_PASSWORD_HASH")
    except Exception:  # noqa: BLE001 – brak pliku secrets.toml lokalnie
        value = None
    return value or os.environ.get("APP_PASSWORD_HASH")


@st.cache_resource
def _fail_log() -> dict[str, list[float]]:
    """Nieudane próby: klucz = publiczny IP klienta albo "*" (cały serwer). Wspólne dla wszystkich sesji."""
    return {}


def _recent(key: str) -> list[float]:
    log = _fail_log()
    cutoff = time.time() - WINDOW_S
    for k in [k for k, v in log.items() if not v or v[-1] <= cutoff]:  # sprzątanie starych wpisów
        del log[k]
    log.setdefault(key, [])
    log[key][:] = [t for t in log[key] if t > cutoff]
    return log[key]


def _client_ip() -> str | None:
    """Publiczny IP klienta. Adres prywatny/lokalny (np. proxy hostingu wspólne dla wszystkich) = None,
    żeby limit na IP nie zamienił się w blokadę wszystkich."""
    try:
        ip = st.context.ip_address
        return ip if ip and ipaddress.ip_address(ip).is_global else None
    except (AttributeError, ValueError):
        return None


def require_password() -> None:
    """Zatrzymuje skrypt, dopóki użytkownik nie poda poprawnego hasła."""
    if st.session_state.get("auth_ok"):
        return
    stored = _stored_hash()
    # cały ekran logowania w jednym kontenerze: po zalogowaniu czyścimy go przed rerunem, inaczej dashboard
    # rysuje się w miejscu elementów logowania, a ich resztki (już bez stylu logowania) rozjeżdżają układ
    root = st.empty()
    with root.container():
        _login_screen(root, stored)
    st.stop()


def _login_screen(root, stored: str | None) -> None:
    left, _, right = st.columns([1.5, 0.08, 1], vertical_alignment="top")
    with left:
        login_ui.hero()
    with right:
        login_ui.form_header()
        if not stored:
            login_ui.error("Dostęp zablokowany: brak APP_PASSWORD_HASH w sekretach aplikacji (patrz README).")
            login_ui.footer()
            st.stop()
        attempts = st.session_state.get("auth_attempts", 0)
        ip = _client_ip()
        if attempts >= MAX_ATTEMPTS or (ip and len(_recent(ip)) >= IP_MAX_FAILS):
            login_ui.error("Za dużo nieudanych prób. Spróbuj ponownie za 15 minut.")
            login_ui.footer()
            st.stop()
        with st.form("login", border=False):
            pwd = st.text_input("Hasło", type="password", autocomplete="current-password", placeholder="Wpisz hasło")
            ok = st.form_submit_button("Zaloguj  →", width="stretch")
        if ok:
            if not pwd:
                login_ui.error("Wpisz hasło.")
            elif verify(pwd, stored):
                st.session_state["auth_ok"] = True
                st.session_state.pop("auth_attempts", None)
                login_ui.loading(root)  # podmienia cały ekran logowania na komunikat do czasu wczytania dashboardu
                st.rerun()
            else:
                st.session_state["auth_attempts"] = attempts + 1
                now = time.time()
                _recent("*").append(now)
                if ip:
                    _recent(ip).append(now)
                # spowalnia zgadywanie; przy ataku (dużo błędów na serwerze) mocniej, ale bez blokowania innych
                time.sleep(SLOW_DELAY_S if len(_recent("*")) >= SLOWDOWN_FAILS else DELAY_S)
                left_n = MAX_ATTEMPTS - attempts - 1
                login_ui.error("Nieprawidłowe hasło. " + (
                    "To była ostatnia próba." if left_n <= 0 else
                    "Pozostała 1 próba." if left_n == 1 else
                    f"Pozostały {left_n} próby." if left_n <= 4 else f"Pozostało {left_n} prób."
                ))
        login_ui.footer()


if __name__ == "__main__":
    import getpass

    print(hash_password(getpass.getpass("Hasło: ")))
