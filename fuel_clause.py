"""Korekta paliwowa (klauzula paliwowa / BAF): indeksy, średnie miesięczne, wyliczenie korekty, umowy w SQLite.

Model klauzuli (najczęstszy w umowach przewozowych):
    zmiana indeksu = średnia indeksu w okresie odniesienia ÷ cena bazowa − 1
    korekta stawki = zmiana indeksu × udział paliwa w stawce (0, gdy |zmiana| < próg)
    nowa stawka    = stawka bazowa × (1 + korekta stawki)
Okres odniesienia dla miesiąca rozliczeniowego M: M−1 (średnia z poprzedniego miesiąca) albo M (bieżący).
Tryb progu: „pełna” – po przekroczeniu progu liczy się cała zmiana; „nadwyżka” – tylko część ponad próg.
"""
import sqlite3
from datetime import date, datetime

import pandas as pd

import db
from sources import eu_oil_bulletin as wob
from sources import orlen

ORLEN_KEY = "orlen"
MODES = {"pełna": "po przekroczeniu progu cała zmiana", "nadwyżka": "tylko zmiana ponad próg"}
LAGS = {1: "średnia z poprzedniego miesiąca (M−1)", 0: "średnia z miesiąca rozliczenia (M)"}
RATE_UNITS = ["EUR/km", "EUR/trasa", "PLN/km", "PLN/trasa", "EUR/t", "PLN/t"]
COLUMNS = ["client", "index_key", "base_value", "base_month", "fuel_share", "threshold", "mode", "lag",
           "rate", "rate_unit", "note"]


# ---------------------------------------------------------------- indeksy
def index_options() -> dict[str, str]:
    """klucz -> etykieta. Orlen hurt + ceny ON na stacjach z biuletynu KE (brutto/netto, EUR/l; Polska też w PLN/l)."""
    opts = {ORLEN_KEY: "Ekodiesel ORLEN – hurt [PLN/m³ netto]"}
    for variant, label in [("brutto", "z podatkami"), ("netto", "bez podatków")]:
        opts[f"wob:{variant}:PL:PLN"] = f"ON na stacjach – Polska, {label} [PLN/l]"
    for code, name in wob.COUNTRIES.items():
        if code == "UK":
            continue
        for variant, label in [("brutto", "z podatkami"), ("netto", "bez podatków")]:
            opts[f"wob:{variant}:{code}:EUR"] = f"ON na stacjach – {name}, {label} [EUR/l]"
    return opts


def index_unit(key: str) -> str:
    if key == ORLEN_KEY:
        return "PLN/m³"
    return "PLN/l" if key.endswith(":PLN") else "EUR/l"


def load_index(key: str) -> pd.DataFrame:
    """Kolumny date, value. Orlen: notowania (cena obowiązuje do kolejnej zmiany); biuletyn KE: tygodniowe."""
    if key == ORLEN_KEY:
        return db.read_series(orlen.SERIES)[["date", "value"]]
    _, variant, code, cur = key.split(":")
    df = db.read_series(wob.series_name(variant, code))[["date", "value"]]
    if cur == "PLN" and not df.empty:
        fx = db.read_series(wob.FX_SERIES).set_index("date")["value"]  # EUR za 1 PLN, z tego samego biuletynu
        df = df.assign(value=df["value"].to_numpy() / fx.reindex(df["date"]).to_numpy()).dropna()
    return df


def monthly(key: str, df: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    """Średnie miesięczne indeksu. Indeks = Period('M'); kolumny avg, n (liczba notowań), complete.
    Orlen: średnia z dni kalendarzowych (każdy dzień = cena obowiązująca tego dnia).
    Biuletyn KE: średnia z biuletynów z danego miesiąca; miesiąc pełny, gdy jest biuletyn z ostatniego poniedziałku."""
    today = today or date.today()
    if df.empty:
        return pd.DataFrame(columns=["avg", "n", "complete"])
    s = df.set_index("date")["value"].sort_index()
    cur = pd.Period(today, "M")
    n = s.groupby(s.index.to_period("M")).size()
    if key == ORLEN_KEY:
        daily = s.reindex(pd.date_range(s.index.min(), pd.Timestamp(today), freq="D"), method="ffill")
        avg = daily.groupby(daily.index.to_period("M")).mean()
        out = pd.DataFrame({"avg": avg, "n": n.reindex(avg.index).fillna(0).astype(int)})
        out["complete"] = out.index < cur
    else:
        avg = s.groupby(s.index.to_period("M")).mean()
        out = pd.DataFrame({"avg": avg, "n": n})
        last_monday = {p: (p.end_time.normalize() - pd.Timedelta(days=p.end_time.dayofweek)) for p in out.index}
        last_obs = s.groupby(s.index.to_period("M")).apply(lambda x: x.index.max())
        out["complete"] = [p < cur and last_obs[p] >= last_monday[p] - pd.Timedelta(days=1) for p in out.index]
    return out


# ---------------------------------------------------------------- wyliczenie
def adjustment(change: float, share: float, threshold: float, mode: str) -> float:
    """Korekta stawki (ułamek) ze zmiany indeksu (ułamek); share i threshold jako ułamki."""
    if abs(change) < threshold:
        return 0.0
    eff = change if mode == "pełna" else (abs(change) - threshold) * (1 if change > 0 else -1)
    return eff * share


def base_value(c: dict, mon: pd.DataFrame) -> tuple[float | None, str]:
    """Cena bazowa z umowy albo średnia indeksu z miesiąca bazowego. Zwraca (wartość, opis)."""
    if pd.notna(c.get("base_value")) and c.get("base_value"):
        return float(c["base_value"]), "z umowy"
    bm = c.get("base_month")
    if bm and pd.notna(bm):
        p = pd.Period(str(bm), "M")
        if p in mon.index:
            return float(mon.at[p, "avg"]), f"średnia {p.strftime('%m.%Y')}"
        return None, f"brak notowań za {p.strftime('%m.%Y')}"
    return None, "brak ceny bazowej i miesiąca bazowego"


def calc(c: dict, mon: pd.DataFrame, bill: pd.Period) -> dict:
    """Wynik klauzuli dla umowy c w miesiącu rozliczeniowym bill."""
    base, base_note = base_value(c, mon)
    lag = c.get("lag")
    ref_p = bill - (1 if lag is None or pd.isna(lag) else int(lag))
    out = {"bill": bill, "ref_month": ref_p, "base": base, "base_note": base_note, "ref": None,
           "change": None, "adj": None, "new_rate": None, "complete": False, "note": ""}
    if base is None or base <= 0:
        out["note"] = base_note
        return out
    if ref_p not in mon.index:
        out["note"] = f"brak notowań za {ref_p.strftime('%m.%Y')}"
        return out
    ref = float(mon.at[ref_p, "avg"])
    change = ref / base - 1
    adj = adjustment(change, float(c["fuel_share"]) / 100, float(c["threshold"]) / 100, c["mode"])
    rate = c.get("rate")
    out.update(ref=ref, change=change, adj=adj, complete=bool(mon.at[ref_p, "complete"]),
               new_rate=None if rate is None or pd.isna(rate) else float(rate) * (1 + adj))
    if not out["complete"]:
        out["note"] = f"{ref_p.strftime('%m.%Y')} jeszcze niepełny – wynik wstępny"
    return out


def history(c: dict, mon: pd.DataFrame, months: int = 12, today: date | None = None) -> pd.DataFrame:
    """Wynik klauzuli dla ostatnich `months` miesięcy rozliczeniowych (od najnowszego)."""
    cur = pd.Period(today or date.today(), "M")
    return pd.DataFrame([calc(c, mon, cur - i) for i in range(months)])


# ---------------------------------------------------------------- umowy (SQLite, ta sama baza co ceny)
def _connect() -> sqlite3.Connection:
    con = db.connect()
    con.execute(
        """CREATE TABLE IF NOT EXISTS fuel_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client TEXT NOT NULL,
            index_key TEXT NOT NULL,
            base_value REAL,             -- cena bazowa w jednostce indeksu (albo NULL -> base_month)
            base_month TEXT,             -- YYYY-MM: baza = średnia indeksu z tego miesiąca
            fuel_share REAL NOT NULL,    -- % udziału paliwa w stawce
            threshold REAL NOT NULL,     -- % progu zadziałania
            mode TEXT NOT NULL,
            lag INTEGER NOT NULL,
            rate REAL,
            rate_unit TEXT,
            note TEXT,
            updated_at TEXT NOT NULL
        )"""
    )
    return con


def load_contracts() -> pd.DataFrame:
    with _connect() as con:
        return pd.read_sql_query(f"SELECT {', '.join(COLUMNS)} FROM fuel_contracts ORDER BY client, id", con)


def _num(v) -> float:
    """Liczba z edytora albo CSV (akceptuje przecinek dziesiętny i spacje tysięcy); brak -> nan."""
    if isinstance(v, str):
        v = v.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    return float(pd.to_numeric(v, errors="coerce"))


def validate(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Porządkuje tabelę umów z edytora/CSV. Zwraca (czyste wiersze, lista błędów); puste wiersze pomija."""
    opts = index_options()
    labels = {v: k for k, v in opts.items()}
    rows, errors = [], []
    for i, r in df.reset_index(drop=True).iterrows():
        client = str(r.get("client") or "").strip()
        if not client or client == "nan":
            continue
        n = f"Wiersz {i + 1} ({client})"
        key = r.get("index_key")
        key = labels.get(key, key)
        if key not in opts:
            errors.append(f"{n}: nieznany indeks „{key}”.")
            continue
        base_v = _num(r.get("base_value"))
        base_m = str(r.get("base_month") or "").strip()
        base_m = "" if base_m in ("nan", "None", "NaT") else base_m[:7]
        if base_m:
            try:
                pd.Period(base_m, "M")
            except ValueError:
                errors.append(f"{n}: miesiąc bazowy „{base_m}” – użyj formatu RRRR-MM.")
                continue
        if pd.isna(base_v) and not base_m:
            errors.append(f"{n}: podaj cenę bazową albo miesiąc bazowy.")
            continue
        share = _num(r.get("fuel_share"))
        thr = _num(r.get("threshold"))
        thr = 0.0 if pd.isna(thr) else float(thr)
        if pd.isna(share) or not 0 < share <= 100 or not 0 <= thr < 100:
            errors.append(f"{n}: udział paliwa 0–100% i próg 0–99%.")
            continue
        mode = r.get("mode") if r.get("mode") in MODES else "pełna"
        lag = _num(r.get("lag"))
        rate = _num(r.get("rate"))
        unit = r.get("rate_unit")
        note = r.get("note")
        rows.append({
            "client": client, "index_key": key, "base_value": None if pd.isna(base_v) else float(base_v),
            "base_month": base_m or None, "fuel_share": float(share), "threshold": thr, "mode": mode,
            "lag": int(lag) if lag in (0, 1) else 1, "rate": None if pd.isna(rate) else float(rate),
            "rate_unit": unit if isinstance(unit, str) and unit else None,
            "note": note if isinstance(note, str) and note else None,
        })
    return pd.DataFrame(rows, columns=COLUMNS), errors


def save_contracts(df: pd.DataFrame) -> None:
    """Zastępuje całą listę umów (edytor pokazuje wszystkie wiersze naraz)."""
    now = datetime.now().isoformat(timespec="seconds")
    with _connect() as con:
        con.execute("DELETE FROM fuel_contracts")
        con.executemany(
            f"INSERT INTO fuel_contracts ({', '.join(COLUMNS)}, updated_at) VALUES ({', '.join('?' * len(COLUMNS))}, ?)",
            [(*[None if pd.isna(v) else v for v in row], now) for row in df[COLUMNS].itertuples(index=False)],
        )
