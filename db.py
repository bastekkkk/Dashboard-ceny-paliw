"""SQLite: wszystkie serie w jednej tabeli, upsert po (series, date) + log pobrań."""
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).parent / "data" / "prices.db"


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute(
        """CREATE TABLE IF NOT EXISTS prices (
            series TEXT NOT NULL,
            date TEXT NOT NULL,          -- YYYY-MM-DD, data notowania
            value REAL NOT NULL,
            unit TEXT NOT NULL,
            source TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (series, date)
        )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS fetch_log (
            series TEXT NOT NULL,
            ts TEXT NOT NULL,
            ok INTEGER NOT NULL,
            message TEXT
        )"""
    )
    return con


def upsert(series: str, df: pd.DataFrame, unit: str, source: str) -> int:
    """df: kolumny 'date' (YYYY-MM-DD) i 'value'. Zwraca liczbę zapisanych wierszy."""
    now = datetime.now().isoformat(timespec="seconds")
    rows = [(series, str(d), float(v), unit, source, now) for d, v in zip(df["date"], df["value"])]
    with connect() as con:
        con.executemany(
            """INSERT INTO prices (series, date, value, unit, source, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(series, date) DO UPDATE SET
                 value=excluded.value, unit=excluded.unit,
                 source=excluded.source, updated_at=excluded.updated_at""",
            rows,
        )
    return len(rows)


def read_series(series: str) -> pd.DataFrame:
    with connect() as con:
        df = pd.read_sql_query(
            "SELECT date, value, unit, source FROM prices WHERE series=? ORDER BY date",
            con,
            params=(series,),
        )
    df["date"] = pd.to_datetime(df["date"])
    return df


def read_series_like(prefix: str) -> pd.DataFrame:
    """Wszystkie serie o nazwie zaczynającej się od prefix (kolumny: series, date, value, unit, source)."""
    with connect() as con:
        df = pd.read_sql_query(
            "SELECT series, date, value, unit, source FROM prices WHERE series LIKE ? ESCAPE '\\' ORDER BY series, date",
            con,
            params=(prefix.replace("_", r"\_") + "%",),
        )
    df["date"] = pd.to_datetime(df["date"])
    return df


def last_date(series: str) -> str | None:
    with connect() as con:
        row = con.execute("SELECT MAX(date) FROM prices WHERE series=?", (series,)).fetchone()
    return row[0]


def log_fetch(series: str, ok: bool, message: str = "") -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO fetch_log (series, ts, ok, message) VALUES (?, ?, ?, ?)",
            (series, datetime.now().isoformat(timespec="seconds"), int(ok), message),
        )


def last_fetch(series: str) -> dict | None:
    with connect() as con:
        row = con.execute(
            "SELECT ts, ok, message FROM fetch_log WHERE series=? ORDER BY ts DESC, rowid DESC LIMIT 1",
            (series,),
        ).fetchone()
    return None if row is None else {"ts": row[0], "ok": bool(row[1]), "message": row[2]}
