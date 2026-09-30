"""Ceny detaliczne oleju napędowego na stacjach w krajach UE – Weekly Oil Bulletin Komisji Europejskiej.

To samo źródło, na którym opierają się zestawienia „ceny paliw w Europie” (m.in. e-petrol.pl; e-petrol
blokuje automatyczne pobieranie przez Cloudflare, dlatego czytamy dane u źródła).
Plik historii (od 2005) jest linkowany ze strony biuletynu; link wyszukujemy na stronie przy każdym
pobraniu, bo KE może zmienić identyfikator dokumentu. Notowania tygodniowe (poniedziałek), publikacja
zwykle w czwartek. Ceny w EUR/1000 l – zapisujemy w EUR/l.
"""
import io
import re

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

PAGE_URL = "https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en"
BASE_URL = "https://energy.ec.europa.eu"
FALLBACK_XLSX = (
    "/document/download/906e60ca-8b6a-44e7-8589-652854d2fd3f_en"
    "?filename=Weekly_Oil_Bulletin_Prices_History_maticni_4web.xlsx"
)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 60
UNIT = "EUR/l"
SOURCE = "Komisja Europejska – Weekly Oil Bulletin"

# wariant -> (arkusz, sufiks kolumny, opis)
VARIANTS = {
    "brutto": ("Prices with taxes", "_price_with_tax_diesel", "z podatkami (cena na pylonie)"),
    "netto": ("Prices wo taxes", "_price_wo_tax_diesel", "bez akcyzy, opłat i VAT"),
}
# krótkie etykiety do przełączników; bez podatków pierwsze = domyślne (porównywalne między krajami i z ARA)
LABELS = {"netto": "Bez podatków", "brutto": "Z podatkami"}
LOG_SERIES = "eu_oil_bulletin"  # nazwa w fetch_log (jedno pobranie = wszystkie kraje)
FX_SERIES = "eu_wob_eur_per_pln"  # kurs z biuletynu: ile EUR za 1 PLN (do przeliczenia na PLN/l)
FX_UNIT = "EUR za 1 PLN"

COUNTRIES = {
    "EU": "Średnia UE-27 (ważona)", "EUR": "Średnia strefy euro (ważona)",
    "AT": "Austria", "BE": "Belgia", "BG": "Bułgaria", "CY": "Cypr", "CZ": "Czechy", "DE": "Niemcy",
    "DK": "Dania", "EE": "Estonia", "ES": "Hiszpania", "FI": "Finlandia", "FR": "Francja", "GR": "Grecja",
    "HR": "Chorwacja", "HU": "Węgry", "IE": "Irlandia", "IT": "Włochy", "LT": "Litwa", "LU": "Luksemburg",
    "LV": "Łotwa", "MT": "Malta", "NL": "Holandia", "PL": "Polska", "PT": "Portugalia", "RO": "Rumunia",
    "SE": "Szwecja", "SI": "Słowenia", "SK": "Słowacja", "UK": "Wielka Brytania (do 2020)",
}
AVERAGES = ("EU", "EUR")


def series_name(variant: str, code: str) -> str:
    return f"eu_on_{variant}_{code}"


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


def _xlsx_url(s: requests.Session) -> str:
    try:
        html = s.get(PAGE_URL, timeout=TIMEOUT).text
        m = re.search(r'href="(/document/download/[^"]+Prices_History[^"]*\.xlsx)"', html)
        if m:
            return BASE_URL + m.group(1).replace("&amp;", "&")
    except requests.RequestException:
        pass
    return BASE_URL + FALLBACK_XLSX


def _parse_sheet(raw: pd.DataFrame, suffix: str, scale: float = 1.0) -> dict[str, pd.DataFrame]:
    """Arkusz ma w wierszu 0 kody kolumn (np. PL_price_with_tax_diesel), dane od wiersza 3, data w kolumnie 0."""
    header = raw.iloc[0]
    dates = pd.to_datetime(raw.iloc[3:, 0], errors="coerce")
    out = {}
    for col, code in header.items():
        if not (isinstance(code, str) and code.endswith(suffix)):
            continue
        country = code.split("_")[0]
        values = pd.to_numeric(raw.iloc[3:, col], errors="coerce") * scale
        df = pd.DataFrame({"date": dates, "value": values}).dropna()
        if df.empty:
            continue
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        out[country] = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return out


def fetch() -> tuple[dict[tuple[str, str], pd.DataFrame], pd.DataFrame]:
    """Zwraca ({(wariant, kod_kraju): df}, df kursu EUR za 1 PLN). Kolumny df: date, value."""
    s = _session()
    url = _xlsx_url(s)
    r = s.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    if not r.content.startswith(b"PK"):
        raise RuntimeError(f"Biuletyn KE: plik nie jest xlsx ({r.headers.get('Content-Type')}): {url}")
    sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=[v[0] for v in VARIANTS.values()], header=None)

    series = {}
    for variant, (sheet, suffix, _) in VARIANTS.items():
        for code, df in _parse_sheet(sheets[sheet], suffix, 1 / 1000).items():  # EUR/1000 l -> EUR/l
            series[(variant, code)] = df
    if ("brutto", "PL") not in series:
        raise RuntimeError("Biuletyn KE: brak kolumny PL_price_with_tax_diesel – zmienił się format pliku")

    fx = _parse_sheet(sheets[VARIANTS["brutto"][0]], "_exchange_rate").get("PL")
    if fx is None:
        raise RuntimeError("Biuletyn KE: brak kolumny PL_exchange_rate – zmienił się format pliku")
    return series, fx
