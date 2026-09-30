"""Ekran logowania w barwach ID Logistics (granat #00417B, czerwień #E2322A z logo)."""
import base64
from functools import lru_cache
from pathlib import Path

import streamlit as st

LOGO_PATH = Path(__file__).parent / "assets" / "id-logistics-logo.jpg"
AUTHOR = "Bastian Jarosz"

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Semi+Condensed:ital,wght@1,800&family=Barlow:wght@400;500;600;700&display=swap');
[data-testid="stAppViewContainer"], [data-testid="stHeader"] { background: #FFFFFF; }
[data-testid="stHeader"] { color: #0B1F33; }
.block-container { max-width: 1360px; padding-top: 2rem; }
[data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] input, [data-testid="stAppViewContainer"] button { font-family: 'Barlow', system-ui, sans-serif; }

.lp { position: relative; overflow: hidden; background: #002A52; color: #FFFFFF; border-radius: 20px;
      min-height: 80vh; padding: 48px 56px 120px; box-sizing: border-box; display: flex; flex-direction: column;
      justify-content: space-between; gap: 32px; font-family: 'Barlow', system-ui, sans-serif; }
.lp svg.bg { position: absolute; inset: 0; width: 100%; height: 100%; }
.lp > div { position: relative; }
.lp .tag { display: flex; align-items: center; gap: 10px; font-size: 14px; font-weight: 600; color: #A9BFD6; }
.lp .tag i { width: 8px; height: 8px; border-radius: 50%; background: #FF7A6E; display: inline-block; }
.lp .eyebrow { font-size: 14px; font-weight: 700; letter-spacing: 0.16em; color: #FF7A6E; margin-bottom: 18px; }
.lp h1 { margin: 0 0 20px; padding: 0; font-family: 'Barlow Semi Condensed', sans-serif; font-style: italic; font-weight: 800;
         font-size: clamp(40px, 4.6vw, 66px); line-height: 0.98; text-transform: uppercase; color: #FFFFFF; }
.lp p.lead { margin: 0 0 20px; font-size: 18px; line-height: 1.5; color: #D3DFEC; max-width: 430px; }
.lp ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 12px; }
.lp li { display: flex; align-items: center; gap: 12px; font-size: 16px; color: #FFFFFF; margin: 0; }
.lp .author { font-size: 15px; color: #FFFFFF; }
.lp .src { font-size: 13px; color: #8FA9C6; margin-top: 6px; }

.lf { display: flex; flex-direction: column; gap: 8px; padding-top: 12vh; }
.lf img { width: 170px; height: auto; margin: 0 0 20px -6px; }
.lf h2 { margin: 0; padding: 0; font-family: 'Barlow', sans-serif; font-size: 34px; font-weight: 700; color: #0B1F33; }
.lf .sub { margin: 0 0 12px; font-size: 16px; color: #4A5B6D; }
[data-testid="stForm"] { border: none; padding: 0; }
[data-testid="stTextInput"] label p { color: #0B1F33; font-size: 14px; font-weight: 600; }
[data-testid="stTextInputRootElement"], [data-testid="stTextInput"] div[data-baseweb="input"] { background: #F6F8FB; border: 1.5px solid #C9D3DE; border-radius: 10px; min-height: 52px; }
[data-testid="stTextInputRootElement"]:focus-within, [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within { border-color: #00417B; box-shadow: 0 0 0 4px rgba(0,65,123,0.14); }
[data-testid="stTextInput"] div[data-baseweb="base-input"] { background: #F6F8FB; }
[data-testid="stTextInput"] input { color: #0B1F33; background: transparent; font-size: 16px; -webkit-text-fill-color: #0B1F33; }
[data-testid="stTextInput"] input::placeholder { color: #6B7B8C; -webkit-text-fill-color: #6B7B8C; }
[data-testid="stTextInput"] button { color: #4A5B6D; background: transparent; }
[data-testid="stFormSubmitButton"] button { background: #00417B; border: none; color: #FFFFFF; min-height: 54px;
                                            border-radius: 10px; font-size: 17px; font-weight: 700; }
[data-testid="stFormSubmitButton"] button:hover { background: #00335F; color: #FFFFFF; }
[data-testid="stFormSubmitButton"] button p { font-size: 17px; font-weight: 700; }
.lerr { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; color: #C62119; margin-top: 4px; }
.lnote { display: flex; align-items: center; gap: 10px; margin-top: 18px; padding-top: 18px; border-top: 1px solid #E3E9F0;
         font-size: 13px; color: #4A5B6D; }
.lnote.author-m { display: none; }

@media (max-width: 640px) {
  .lp { min-height: 0; padding: 32px 24px 48px; border-radius: 16px; }
  .lp p.lead, .lp ul, .lp .src, .lp .tag, .lp .author { display: none; }
  .lp .eyebrow { margin-bottom: 10px; }
  .lp h1 { margin: 0; font-size: 38px; }
  .lf { padding-top: 8px; }
  .lf img { width: 120px; margin-bottom: 12px; }
  .lf h2 { font-size: 28px; }
  .lnote.author-m { display: flex; border-top: none; padding-top: 0; margin-top: 4px; }
}
</style>
"""

CHECK = ('<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#FF7A6E" stroke-width="2.4" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12l5 5L20 6"/></svg>')
LOCK = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#00417B" stroke-width="2" '
        'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="11" width="16" height="10" rx="2"/>'
        '<path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>')
ALERT = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" '
         'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/>'
         '<path d="M12 8v5"/><path d="M12 16.5v.01"/></svg>')

BG = """
<svg class="bg" viewBox="0 0 820 900" preserveAspectRatio="xMaxYMax slice" aria-hidden="true">
<g stroke="#0B3A68" stroke-width="1" fill="none">
<path d="M0 180 L820 120"/><path d="M0 360 L820 330"/><path d="M0 540 L820 560"/><path d="M0 720 L820 780"/>
<path d="M140 0 L100 900"/><path d="M340 0 L330 900"/><path d="M540 0 L560 900"/><path d="M740 0 L790 900"/>
</g>
<g transform="translate(-48 0)"><path d="M-40 905 C 300 885, 560 825, 700 625 C 760 535, 788 430, 798 318" fill="none" stroke="#E2322A" stroke-width="24" stroke-linecap="round"/>
<path d="M770 330 L800 238 L830 332 Z" fill="#E2322A"/></g>
</svg>
"""


@lru_cache(maxsize=1)
def logo_src() -> str:
    if not LOGO_PATH.exists():
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(LOGO_PATH.read_bytes()).decode()


def _html(markup: str) -> None:
    st.markdown("".join(line.strip() for line in markup.splitlines()), unsafe_allow_html=True)


def hero() -> None:
    _html(CSS)
    _html(f"""
    <div class="lp">{BG}
      <div class="tag"><i></i>Narzędzie wewnętrzne · Dział transportu</div>
      <div>
        <div class="eyebrow">MONITORING CEN PALIW</div>
        <h1>Polska<br>i Europa<br>na jednym ekranie.</h1>
        <p class="lead">Bieżące ceny paliw dla zespołu transportu: hurt w Polsce, giełda ARA i ceny ON na stacjach w krajach UE.</p>
        <ul>
          <li>{CHECK}Hurt ORLEN i giełda ARA – codziennie</li>
          <li>{CHECK}Ceny ON na stacjach w 27 krajach UE – co tydzień</li>
          <li>{CHECK}Porównanie krajów na trasie i premia PL vs ARA</li>
        </ul>
      </div>
      <div>
        <div class="author">Autor aplikacji: <b>{AUTHOR}</b></div>
        <div class="src">Źródła: ORLEN · ICE LS Gasoil · Komisja Europejska (Weekly Oil Bulletin) · Yahoo Finance</div>
      </div>
    </div>""")


def form_header() -> None:
    logo = logo_src()
    img = f'<img src="{logo}" alt="ID Logistics">' if logo else ""
    _html(f'<div class="lf">{img}<h2>Zaloguj się</h2><p class="sub">Dostęp tylko dla zespołu ID Logistics.</p></div>')


def error(msg: str) -> None:
    _html(f'<div class="lerr" role="alert">{ALERT}<span>{msg}</span></div>')


def footer() -> None:
    _html(f'<div class="lnote">{LOCK}<span>Połączenie szyfrowane · limit prób logowania</span></div>'
          f'<div class="lnote author-m"><span>Autor aplikacji: <b>{AUTHOR}</b></span></div>')
