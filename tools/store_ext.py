"""tools/store_ext.py - this agent's own tables, plus loaders for both the
bundled fixtures (`make demo`, tests) and a hotel's own CSV exports (live).

`migrate(store)` is called once, right after `Store(settings)`, exactly as
`core/store.py` documents. Everything else here is I/O - the pure engine in
tools/esg_report.py never touches a file or a database directly.
"""

from __future__ import annotations

import csv
import json
import uuid
from pathlib import Path

from esg_report import DayReading, ZoneReading

SCHEMA = """
CREATE TABLE IF NOT EXISTS sustain_daily (
  date TEXT PRIMARY KEY, kwh REAL DEFAULT 0, water_m3 REAL DEFAULT 0,
  waste_kg REAL DEFAULT 0, laundry_kg REAL DEFAULT 0, occupied_rooms INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sustain_zone_daily (
  id TEXT PRIMARY KEY, date TEXT NOT NULL, zone TEXT NOT NULL,
  kwh REAL DEFAULT 0, water_m3 REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sustain_runs (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
  stats_json TEXT, narrative TEXT
);
CREATE TABLE IF NOT EXISTS sustain_recommendations (
  id TEXT PRIMARY KEY, created_at TEXT NOT NULL, period_label TEXT NOT NULL,
  title TEXT, metric TEXT, impact REAL DEFAULT 0
);
"""


def migrate(store) -> None:
    store.migrate(SCHEMA)


def _count(store, table: str) -> int:
    row = store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return row["n"] if row else 0


def _row_get(row: dict, *names: str, default=""):
    norm = {"".join(ch for ch in k.lower() if ch.isalnum()): v for k, v in row.items() if k}
    for name in names:
        key = "".join(ch for ch in name.lower() if ch.isalnum())
        if key in norm and norm[key] not in (None, ""):
            return norm[key]
    return default


def _num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "").strip() or default)
    except (TypeError, ValueError):
        return default


def _int(value, default: int = 0) -> int:
    try:
        return int(float(str(value) or default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# fixtures (make demo, tests) - JSON in, tables filled once
# --------------------------------------------------------------------------
def seed_fixtures(store, fixtures_dir: Path) -> dict:
    """Load the bundled fixtures into the tables above, if they are empty.

    Safe to call on every run: each table is only filled the first time, so a
    hotel's own imported data is never overwritten by re-running `make demo`.
    """
    loaded = {}
    plan = [
        ("sustain_daily", "sustain_daily.json", _insert_daily),
        ("sustain_zone_daily", "sustain_zone_daily.json", _insert_zone_daily),
    ]
    for table, filename, inserter in plan:
        if _count(store, table) > 0:
            loaded[table] = _count(store, table)
            continue
        path = fixtures_dir / filename
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        inserter(store, rows)
        loaded[table] = len(rows)
    return loaded


def _insert_daily(store, rows: list[dict]) -> None:
    for r in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO sustain_daily (date, kwh, water_m3, waste_kg, laundry_kg, "
            "occupied_rooms) VALUES (?,?,?,?,?,?)",
            (r["date"], r.get("kwh", 0), r.get("water_m3", 0), r.get("waste_kg", 0),
             r.get("laundry_kg", 0), r.get("occupied_rooms", 0)))


def _insert_zone_daily(store, rows: list[dict]) -> None:
    for r in rows:
        row_id = r.get("id") or f"{r['date']}:{r['zone']}"
        store.db.execute(
            "INSERT OR IGNORE INTO sustain_zone_daily (id, date, zone, kwh, water_m3) "
            "VALUES (?,?,?,?,?)",
            (row_id, r["date"], r["zone"], r.get("kwh", 0), r.get("water_m3", 0)))


# --------------------------------------------------------------------------
# loaders: table rows -> engine dataclasses (tools/run.py uses these)
# --------------------------------------------------------------------------
def load_sustain_daily(store) -> list[DayReading]:
    rows = store.db.execute("SELECT * FROM sustain_daily ORDER BY date ASC").fetchall()
    return [DayReading(date=r["date"], kwh=r["kwh"], water_m3=r["water_m3"],
                       waste_kg=r["waste_kg"], laundry_kg=r["laundry_kg"],
                       occupied_rooms=r["occupied_rooms"]) for r in rows]


def load_sustain_zone_daily(store) -> list[ZoneReading]:
    rows = store.db.execute("SELECT * FROM sustain_zone_daily ORDER BY date ASC").fetchall()
    return [ZoneReading(date=r["date"], zone=r["zone"], kwh=r["kwh"], water_m3=r["water_m3"])
           for r in rows]


def record_recommendation(store, period_label: str, title: str, metric: str, impact: float) -> None:
    from core.store import utcnow
    store.db.execute(
        "INSERT INTO sustain_recommendations (id, created_at, period_label, title, metric, impact) "
        "VALUES (?,?,?,?,?,?)",
        (uuid.uuid4().hex, utcnow(), period_label, title, metric, impact))


def list_recommendations(store, limit: int = 200) -> list[dict]:
    rows = store.db.execute(
        "SELECT * FROM sustain_recommendations ORDER BY created_at ASC LIMIT ?",
        (int(limit),)).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# live path: a hotel's own CSV exports in data/imports/
# --------------------------------------------------------------------------
def import_sustain_daily_csv(store, path: Path) -> int:
    """`date,kwh,water_m3,waste_kg,laundry_kg,occupied_rooms` rows, one per
    day. Accumulates by date - never clears, so a monthly export just keeps
    extending the meter table."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    n = 0
    for r in rows:
        d = str(_row_get(r, "date"))[:10]
        if not d:
            continue
        store.db.execute(
            "INSERT INTO sustain_daily (date, kwh, water_m3, waste_kg, laundry_kg, "
            "occupied_rooms) VALUES (?,?,?,?,?,?) ON CONFLICT(date) DO UPDATE SET "
            "kwh=excluded.kwh, water_m3=excluded.water_m3, waste_kg=excluded.waste_kg, "
            "laundry_kg=excluded.laundry_kg, occupied_rooms=excluded.occupied_rooms",
            (d, _num(_row_get(r, "kwh")), _num(_row_get(r, "water_m3", "water")),
             _num(_row_get(r, "waste_kg", "waste")), _num(_row_get(r, "laundry_kg", "laundry")),
             _int(_row_get(r, "occupied_rooms", "rooms"))))
        n += 1
    return n


def import_sustain_zone_daily_csv(store, path: Path) -> int:
    """`date,zone,kwh,water_m3` rows - optional per-zone sub-metering.
    Missing entirely is normal; see docs/integrations.md."""
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        rows = [dict(r) for r in csv.DictReader(fh)]
    n = 0
    for r in rows:
        d = str(_row_get(r, "date"))[:10]
        zone = str(_row_get(r, "zone"))
        if not d or not zone:
            continue
        store.db.execute(
            "INSERT OR REPLACE INTO sustain_zone_daily (id, date, zone, kwh, water_m3) "
            "VALUES (?,?,?,?,?)",
            (f"{d}:{zone}", d, zone, _num(_row_get(r, "kwh")), _num(_row_get(r, "water_m3", "water"))))
        n += 1
    return n
