"""Bramka hasła. W repo nie ma ani hasła, ani jego skrótu – tylko w sekretach (Streamlit Secrets / zmienna środowiskowa).

Skrót: scrypt (sól 16 B, n=2^15, r=8, p=1), format `scrypt$n$r$p$sól_b64$skrót_b64`.
Nowy skrót: `python auth.py` (hasło wpisujesz niewidocznie), wynik wklej jako APP_PASSWORD_HASH.
Brak APP_PASSWORD_HASH = aplikacja zablokowana (fail closed), żeby zapomniany sekret nie otwierał danych.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

import streamlit as st

MAX_ATTEMPTS = 5  # na sesję przeglądarki
GLOBAL_MAX_FAILS = 30  # na cały serwer w oknie GLOBAL_WINDOW_S (nowa karta nie resetuje limitu)
GLOBAL_WINDOW_S = 15 * 60
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
def _global_fails() -> list[float]:
    return []


def _recent_fails() -> list[float]:
    fails = _global_fails()
    cutoff = time.time() - GLOBAL_WINDOW_S
    fails[:] = [t for t in fails if t > cutoff]
    return fails


def require_password() -> None:
    """Zatrzymuje skrypt, dopóki użytkownik nie poda poprawnego hasła."""
    if st.session_state.get("auth_ok"):
        return
    stored = _stored_hash()
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid:
        st.markdown("### Ceny paliw")
        if not stored:
            st.error("Dostęp zablokowany: brak APP_PASSWORD_HASH w sekretach aplikacji (patrz README).")
            st.stop()
        attempts = st.session_state.get("auth_attempts", 0)
        if attempts >= MAX_ATTEMPTS or len(_recent_fails()) >= GLOBAL_MAX_FAILS:
            st.error("Za dużo nieudanych prób. Odśwież stronę za kilka minut.")
            st.stop()
        with st.form("login"):
            pwd = st.text_input("Hasło", type="password", autocomplete="current-password")
            ok = st.form_submit_button("Zaloguj", type="primary", width="stretch")
        if ok:
            if verify(pwd, stored):
                st.session_state["auth_ok"] = True
                st.session_state.pop("auth_attempts", None)
                st.rerun()
            st.session_state["auth_attempts"] = attempts + 1
            _recent_fails().append(time.time())
            time.sleep(1.5)  # spowalnia zgadywanie
            st.error("Nieprawidłowe hasło.")
    st.stop()


if __name__ == "__main__":
    import getpass

    print(hash_password(getpass.getpass("Hasło: ")))
