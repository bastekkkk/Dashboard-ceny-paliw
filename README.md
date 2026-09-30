# Dashboard hurtowych cen paliw

Lokalny dashboard Streamlit: Ekodiesel ORLEN (hurt), ARA (ICE Low Sulphur Gasoil), złoto/srebro/USD‑PLN, premia PL vs ARA oraz ceny ON na stacjach w krajach UE. Dane w SQLite (`data/prices.db`).

## Uruchomienie (5 komend)

```bash
git clone https://github.com/bastekkkk/Dashboard-ceny-paliw.git && cd Dashboard-ceny-paliw
python -m venv .venv && export OILPRICEAPI_KEY=twoj_klucz   # Windows: setx OILPRICEAPI_KEY twoj_klucz (nowe okno)
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python update_data.py                  # pierwsze pobranie pełnej historii (~15 s)
.venv/bin/streamlit run app.py                   # http://localhost:8501
```

## Odświeżanie danych

- Ręcznie: `python update_data.py` albo przycisk **„Odśwież dane”** w aplikacji (ten sam kod).
- cron (Linux/macOS), pon–sob 18:30: `30 18 * * 1-6 cd /ścieżka/do/repo && .venv/bin/python update_data.py`
- Windows: Harmonogram zadań → akcja `.venv\Scripts\python.exe update_data.py`, katalog startowy = repo.
- **ARA** pobiera się z [OilPriceAPI](https://www.oilpriceapi.com) (kod `GASOIL_USD`). Darmowy klucz:
  https://www.oilpriceapi.com/auth/signup → zmienna środowiskowa `OILPRICEAPI_KEY`. Skrypt zużywa 1 zapytanie
  na uruchomienie (2 przy pierwszym). Uruchamiaj po zamknięciu ICE (~18:30), żeby zapisać ostatnią cenę dnia, a nie śródsesyjną.
  Pierwsze uruchomienie importuje ~30 dni **średnich dziennych** (oznaczone w źródle) – to nie są ceny zamknięcia.
  Nowe konto startuje na 7-dniowym trialu; czy plan Free obejmuje `GASOIL_USD`, okaże się po trialu (HTTP 402 = nie).
- **Ceny ON w UE** – plik historii Weekly Oil Bulletin KE (~4 MB, od 2005). Nowy biuletyn raz w tygodniu, więc
  skrypt pobiera plik tylko, gdy ostatnie notowanie w bazie ma ≥ 10 dni; przy kolejnych pobraniach nadpisuje ostatnie 8 tygodni
  (KE koryguje dane wstecz). e-petrol.pl pokazuje te same dane, ale blokuje automatyczne pobieranie (Cloudflare).
- Awaryjnie: wpis ręczny w sekcji 2 (rozwijany formularz) z
  [TradingView ICEEUR:ULS1!](https://www.tradingview.com/symbols/ICEEUR-ULS1!/). TradingView nie jest pobierany
  automatycznie – regulamin zabrania automatycznego użycia danych.
- Licencja: OilPriceAPI udostępnia notowania ICE **tylko do użytku wewnętrznego** – bez publicznego wyświetlania i redystrybucji.

## Źródła i jednostki

| Seria | Źródło | Jednostka | Historia |
|---|---|---|---|
| Ekodiesel ORLEN | `tool.orlen.pl/api/wholesalefuelprices` (API widgetu na orlen.pl, productId=43) | PLN/m³ bez VAT, 15°C | od 2004-01-01 |
| ARA – ICE LS Gasoil | OilPriceAPI `GASOIL_USD` (awaryjnie wpis ręczny z TradingView) | USD/t | 30 dni wstecz przy 1. pobraniu, potem własne snapshoty |
| Złoto | Yahoo Finance `GC=F` | USD/oz | od 2000-08-30 |
| Srebro | Yahoo Finance `SI=F` | USD/oz | od 2000-08-30 |
| USD/PLN | Yahoo Finance `USDPLN=X` | PLN za 1 USD | od 2003-12-01 |
| ON na stacjach, UE-27 + średnie UE/strefa euro | [Weekly Oil Bulletin KE](https://energy.ec.europa.eu/data-and-analysis/weekly-oil-bulletin_en), arkusze „Prices with taxes” / „Prices wo taxes” | EUR/l (PLN/l kursem z biuletynu) | tygodniowo od 2005-01-03 |

Premia: `ARA [PLN/m³] = USD/t × USD/PLN ÷ 1,1834` (gęstość 0,845 kg/l). Różnica Orlen − ARA zawiera podatki, opłaty, logistykę i marżę.
Historia premii: punkt w każdy dzień z notowaniem ARA (Orlen i USD/PLN z tego dnia lub ostatniego wcześniejszego notowania). Punkty ze średniej dziennej ARA (import historii) są oznaczone linią kropkowaną.

## Struktura

```
app.py            UI Streamlit: kafelki KPI + zakładki (Przegląd, Hurt ORLEN, ARA, Premia, Stacje UE, Rynki); czyta tylko z bazy, cache 15 min
ui.py             motyw (ciemny), karty HTML, sparklines, wspólny styl wykresów Plotly
.streamlit/       config.toml – kolory motywu
update_data.py    pobieranie Orlen + Yahoo -> SQLite (upsert po serii i dacie)
db.py             SQLite: tabele prices i fetch_log
sources/          orlen.py, yahoo.py, oilpriceapi.py, ara_manual.py, eu_oil_bulletin.py
```
