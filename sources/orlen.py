"""Hurtowa cena Orlen Ekodiesel (PLN netto / m³) z publicznego API widgetu cen na orlen.pl.

Endpoint jest tym samym, którego używa strona https://www.orlen.pl/pl/dla-biznesu/hurtowe-ceny-paliw.
WAF Orlenu odrzuca zapytania bez nagłówków przeglądarki (Origin/Referer), stąd HEADERS.
"""
from datetime import date

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SERIES = "orlen_ekodiesel"
UNIT = "PLN/m³ netto"
SOURCE = "ORLEN – hurtowe ceny paliw (tool.orlen.pl, productId=43)"
PRODUCT_ID = 43  # "Olej Napędowy Ekodiesel", symbol ONEkodiesel
URL = "https://tool.orlen.pl/api/wholesalefuelprices/ByProduct"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.orlen.pl",
    "Referer": "https://www.orlen.pl/",
}
TIMEOUT = 30
FIRST_DATE = "2004-01-01"  # najstarsze notowanie zwracane przez API (zweryfikowane)


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


def fetch(start: str = FIRST_DATE, end: str | None = None) -> pd.DataFrame:
    end = end or date.today().isoformat()
    r = _session().get(URL, params={"productId": PRODUCT_ID, "from": start, "to": end}, timeout=TIMEOUT)
    r.raise_for_status()
    if "json" not in r.headers.get("Content-Type", ""):
        raise RuntimeError(f"Orlen API zwróciło nie-JSON (prawdopodobnie blokada WAF): {r.text[:150]}")
    data = r.json()
    if not data:
        raise RuntimeError(f"Orlen API zwróciło pustą listę dla zakresu {start}..{end}")
    for row in data:
        if row.get("productName") != "ONEkodiesel":
            raise RuntimeError(f"Nieoczekiwany produkt w odpowiedzi: {row.get('productName')}")
        if row.get("unit") not in (None, "PLN/m3"):
            raise RuntimeError(f"Nieoczekiwana jednostka w odpowiedzi: {row.get('unit')}")
    df = pd.DataFrame(
        {"date": [row["effectiveDate"][:10] for row in data], "value": [row["value"] for row in data]}
    )
    return df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
