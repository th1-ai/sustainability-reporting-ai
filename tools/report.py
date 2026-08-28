#!/usr/bin/env python3
"""tools/report.py - what the agent did, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --since 2026-08-01

Reads `sustain_runs` (one row per report run), the `items` queue, and
`sustain_recommendations` - no recomputation, just what actually happened.
This is the evidence behind the roster's ROI line, "-9% Utility cost per
occupied room" - see docs/benefits.md for how to read it and its honest
caveat: proposed savings are logged, not proven to have caused what happened
next.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402


def runs(store, since: str | None) -> list[dict]:
    sql = "SELECT * FROM sustain_runs"
    params: list = []
    if since:
        sql += " WHERE created_at >= ?"
        params.append(since)
    sql += " ORDER BY created_at ASC"
    return [{"created_at": r["created_at"], "stats": json.loads(r["stats_json"] or "{}"),
            "narrative": r["narrative"]} for r in store.db.execute(sql, params).fetchall()]


def edit_rate(store) -> tuple[int, int]:
    """(edited count, sent+auto_sent count) across esg_report items."""
    was_edited = store.db.execute(
        "SELECT COUNT(DISTINCT item_id) AS n FROM events WHERE action='status:edited'").fetchone()
    total_sent = store.db.execute(
        "SELECT COUNT(*) AS n FROM items WHERE kind='esg_report' "
        "AND review_status IN ('edited','sent')").fetchone()
    return (was_edited["n"] if was_edited else 0, total_sent["n"] if total_sent else 0)


def cost_trend(store) -> list[dict]:
    rows = store.db.execute(
        "SELECT payload_json, created_at FROM items WHERE kind='esg_report' "
        "ORDER BY created_at ASC").fetchall()
    out = []
    for r in rows:
        payload = json.loads(r["payload_json"] or "{}")
        out.append({"created_at": r["created_at"], "period": payload.get("period_label", ""),
                   "cost_per_room": payload.get("cost_per_room", 0)})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=None, help="ISO date/time - only runs on or after this")
    args = ap.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        store_ext.migrate(store)
        run_rows = runs(store, args.since)
        print(f"Sustainability Reporting AI - activity report"
             f"{f' since {args.since}' if args.since else ''}")
        print("=" * 60)

        print(f"\nMonthly ESG Report: {len(run_rows)} run(s)")
        for r in run_rows[-3:]:
            s = r["stats"]
            print(f"  {r['created_at']}  {s.get('period', '?')}  {s.get('anomalies', 0)} "
                 f"anomaly alert(s)  exported={s.get('exported', False)}")

        edited, sent = edit_rate(store)
        pct = round(edited / sent * 100, 1) if sent else 0.0
        print(f"\nHuman edit rate on the report: {edited}/{sent} ({pct}%)")

        alert_counts = store.db.execute(
            "SELECT review_status, COUNT(*) AS n FROM items WHERE kind='engineering_alert' "
            "GROUP BY review_status").fetchall()
        if alert_counts:
            print("\nEngineering alerts by status:")
            for row in alert_counts:
                print(f"  {row['review_status']}: {row['n']}")

        trend = cost_trend(store)
        currency = settings.hotel.currency
        print(f"\nUtility cost per occupied room, by report period ({currency}):")
        for t in trend[-6:]:
            print(f"  {t['period'] or '?'}: {t['cost_per_room']:,.2f}")
        if len(trend) >= 2:
            before, now = trend[-2]["cost_per_room"], trend[-1]["cost_per_room"]
            pct_move = round((now - before) / before * 100, 1) if before else 0.0
            print(f"  change vs prior report: {pct_move:+.1f}% "
                 f"(roster target: -9%, see docs/benefits.md)")

        recs = store_ext.list_recommendations(store)
        proposed_total = round(sum(r["impact"] for r in recs), 2)
        print(f"\nRecommendations logged: {len(recs)}, "
             f"{proposed_total:,.2f} {currency}/year proposed (cumulative, all periods).")
        print("Honest caveat: this is what was PROPOSED, not proof that a saving happened - "
             "nothing here ties a recommendation to the cost-per-room change above. See "
             "docs/benefits.md 'What this does not prove'.")

        usage = store.usage_totals(since=args.since)
        print(f"\nLLM usage (governance note only): {usage['calls']} call(s), "
             f"{usage['input_tokens']} in / {usage['output_tokens']} out tokens, "
             f"${usage['cost_usd']:.4f}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
