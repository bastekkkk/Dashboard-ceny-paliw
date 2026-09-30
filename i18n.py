"""Język interfejsu (PL/EN). Wybór w nagłówku dashboardu i na ekranie logowania, zapamiętany w adresie (?lang=en),
więc przetrwa odświeżenie strony i wylogowanie. Teksty trzymamy obok siebie w kodzie: L("po polsku", "in English")."""
import streamlit as st

LANGS = {"pl": "PL", "en": "EN"}
DEFAULT = "pl"
_KEY, _PICK = "lang", "_lang_pick"


def lang() -> str:
    if _KEY not in st.session_state:
        q = st.query_params.get(_KEY, DEFAULT)
        st.session_state[_KEY] = q if q in LANGS else DEFAULT
    return st.session_state[_KEY]


def en() -> bool:
    return lang() == "en"


def L(pl: str, en_: str) -> str:
    """Tekst w bieżącym języku."""
    return en_ if en() else pl


def _on_pick() -> None:
    value = st.session_state.get(_PICK)
    if value in LANGS:  # ponowny klik w wybrany język odznacza przycisk (None) – wtedy nic nie zmieniamy
        st.session_state[_KEY] = value
        st.query_params[_KEY] = value


def picker(container=st) -> None:
    """Przełącznik PL | EN."""
    st.session_state[_PICK] = lang()  # przed utworzeniem widżetu: zawsze pokazuje bieżący język
    container.segmented_control("Język / Language", list(LANGS), format_func=LANGS.get, key=_PICK,
                                on_change=_on_pick, label_visibility="collapsed")
