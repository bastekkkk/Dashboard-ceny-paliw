# Dashboard hurtowych cen paliw

Lokalny dashboard Streamlit: Ekodiesel ORLEN (hurt), ARA (ICE Low Sulphur Gasoil), złoto/srebro/USD‑PLN oraz premia PL vs ARA. Dane w SQLite (`data/prices.db`).

## Uruchomienie (5 komend)

```bash
git clone https://github.com/bastekkkk/dashboard-ceny-paliw.git && cd dashboard-ceny-paliw
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip install -r requirements.txt
.venv/bin/python update_data.py                  # pierwsze pobranie pełnej historii (~5 s)
.venv/bin/streamlit run app.py                   # http://localhost:8501
```

## Odświeżanie danych

- Ręcznie: `python update_data.py` albo przycisk **„Odśwież dane”** w aplikacji (ten sam kod).
- cron (Linux/macOS), pon–sob 18:30: `30 18 * * 1-6 cd /ścieżka/do/repo && .venv/bin/python update_data.py`
- Windows: Harmonogram zadań → akcja `.venv\Scripts\python.exe update_data.py`, katalog startowy = repo.
- **ARA wpisujesz ręcznie** raz dziennie w sekcji 2 (cena zamknięcia / ostatnia z
  [TradingView ICEEUR:ULS1!](https://www.tradingview.com/symbols/ICEEUR-ULS1!/), USD/t).

## Źródła i jednostki

| Seria | Źródło | Jednostka | Historia |
|---|---|---|---|
| Ekodiesel ORLEN | `tool.orlen.pl/api/wholesalefuelprices` (API widgetu na orlen.pl, productId=43) | PLN/m³ bez VAT, 15°C | od 2004-01-01 |
| ARA – ICE LS Gasoil | wpis ręczny z TradingView `ICEEUR:ULS1!` | USD/t | od pierwszego wpisu |
| Złoto | Yahoo Finance `GC=F` | USD/oz | od 2000-08-30 |
| Srebro | Yahoo Finance `SI=F` | USD/oz | od 2000-08-30 |
| USD/PLN | Yahoo Finance `USDPLN=X` | PLN za 1 USD | od 2003-12-01 |

Premia: `ARA [PLN/m³] = USD/t × USD/PLN ÷ 1,1834` (gęstość 0,845 kg/l). Różnica Orlen − ARA zawiera podatki, opłaty, logistykę i marżę.

## Struktura

```
app.py            UI Streamlit (czyta tylko z bazy, cache 15 min)
update_data.py    pobieranie Orlen + Yahoo -> SQLite (upsert po serii i dacie)
db.py             SQLite: tabele prices i fetch_log
sources/          orlen.py, yahoo.py, ara_manual.py
```
