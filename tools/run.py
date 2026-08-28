#!/usr/bin/env python3
"""tools/run.py - Sustainability Reporting AI's main loop.

    python3 tools/run.py --once                 # run if the monthly job is due
    python3 tools/run.py --once --report         # force the monthly ESG report
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run

One pass: import the meter CSVs, run the deterministic engine, queue the
report for review, queue one engineering alert per flagged anomaly, export
one row to the consumption dashboard, and ask the LLM for a cosmetic
governance note. Nothing is sent - workflows/80-review.md and docs/safety.md
cover the review queue and the shadow/live switch.

Exit codes: 0 ok, 1 a real error. There is no exit code 3 here: the only LLM
call is cosmetic and is skipped entirely on the `interactive` provider rather
than pausing the run for it - see docs/how-it-works.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, complete  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, utcnow  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import esg_report  # noqa: E402
import store_ext  # noqa: E402

log = get_logger("run")
NOTE_SCHEMA = json.loads((REPO_ROOT / "prompts" / "schemas" / "governance-note.json")
                        .read_text(encoding="utf-8"))
CADENCE_DAYS = {"every-15-min": 15 / 1440, "hourly": 1 / 24, "nightly": 1, "morning": 1,
               "weekly": 7, "monthly": 30}


def cadence_of(schedule: dict, key: str, default: str) -> str:
    value = schedule.get(key, default)
    if isinstance(value, dict):
        return str(value.get("cadence") or value.get("cron") or value.get("every") or default)
    return str(value or default)


def is_due(store, key: str, cadence: str, force: bool) -> bool:
    if force:
        return True
    last = store.get_cursor(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    days = (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400
    return days >= CADENCE_DAYS.get(cadence, 30) - 0.5


def get_governance_note(settings, store, item_id: str, summary: dict, *, dry_run: bool,
                        provider: str | None, fixture_id: str) -> str | None:
    """Cosmetic 2-3 sentence note. Never gates a decision - see docs/how-it-works.md.

    Skipped entirely (not paused) on the `interactive` provider, and swallowed
    on any other failure, so a run always completes with or without it.
    """
    effective = provider or settings.llm.provider
    if dry_run or effective == "interactive":
        return None
    try:
        prompt = build_prompt("governance-note", settings=settings, item=summary,
                              fixture_id=fixture_id)
        result = complete("governance-note", prompt, schema=NOTE_SCHEMA, settings=settings,
                          provider=provider, store=store, item_id=item_id, effort="low")
        return (result.data or {}).get("note")
    except LLMError as exc:
        log.warn("governance note skipped", error=str(exc)[:200])
        return None


def export_dashboard(settings, result: "esg_report.EsgReportResult") -> bool:
    """Append one row to the esg_dashboard CSV/Sheets export.

    Gated exactly like every other write in this repo: `Sheets.append` is
    `@guarded_write("sheets_write")`, so `mode: shadow` blocks it the same
    way it blocks an email send. `sheets_write` is NOT in
    `review.require_approval_for` by default, so once mode is live this
    happens automatically, every run, with no separate approval step - it is
    a log-only export, not a guest- or engineering-facing action.
    """
    from core.adapters import get_sheets
    sheets = get_sheets(settings)
    try:
        existing = sheets.read("esg_dashboard")
        rows = ([esg_report.DASHBOARD_HEADER] if not existing else [])
        rows += esg_report.export_dashboard_rows(settings.hotel.name, result)
        sheets.append("esg_dashboard", rows)
        return True
    except WriteBlocked as exc:
        log.info("dashboard export blocked", reason=str(exc)[:160])
        return False


def record_run(store, kind: str, stats: dict, narrative: str | None) -> None:
    import uuid
    store.db.execute(
        "INSERT INTO sustain_runs (id, created_at, kind, stats_json, narrative) VALUES (?,?,?,?,?)",
        (uuid.uuid4().hex, utcnow(), kind, json.dumps(stats, ensure_ascii=False), narrative))


def run_esg_report(settings, store, *, provider: str | None, dry_run: bool,
                   source: str = "live") -> int:
    """Builds the monthly ESG report and any engineering alerts. Returns the
    count of items drafted or queued (report + alerts).

    ``source="live"`` (the default, used by `tools/run.py`'s own main loop) is
    the only mode that re-imports a hotel's own ``data/imports/*.csv`` before
    building the report - always resolved from ``settings.root``, never a
    module-level constant, so a sandboxed test (`AGENT_REPO_ROOT`) or a
    separate demo root can never see a hotel's real files.

    ``source="demo"`` (`tools/demo.py` only) skips the CSV import step
    entirely and reports on whatever is already in `store` - the bundled
    fixtures `tools/demo.py` seeds beforehand, and nothing else. This is what
    keeps `make demo` "regardless of config/hotel.yaml" true for the DATA too,
    not just the provider/adapters - see docs/how-it-works.md and Finding 4.
    """
    imports_dir = settings.root / "data" / "imports"
    if not dry_run and source != "demo":
        n_daily = store_ext.import_sustain_daily_csv(store, imports_dir / "sustain_daily.csv")
        if n_daily:
            log.info("imported sustain_daily.csv", rows=n_daily)
        n_zone = store_ext.import_sustain_zone_daily_csv(store, imports_dir / "sustain_zone_daily.csv")
        if n_zone:
            log.info("imported sustain_zone_daily.csv", rows=n_zone)
    rows = store_ext.load_sustain_daily(store)
    if not rows:
        log.warn("no sustain_daily rows - nothing to report",
                hint="import data/imports/sustain_daily.csv, see docs/integrations.md")
        return 0
    zone_rows = store_ext.load_sustain_zone_daily(store)

    anomaly_enabled = bool(settings.agent_get("anomaly.rules.esg-anomaly", True))
    result = esg_report.build_report(
        rows, zone_rows, tariffs=settings.agent_get("tariffs", {}), anomaly_enabled=anomaly_enabled,
        flag_sigma=float(settings.agent_get("anomaly.flag_sigma", esg_report.DEFAULT_FLAG_SIGMA)),
        escalate_sigma=float(settings.agent_get("anomaly.escalate_sigma",
                                                esg_report.DEFAULT_ESCALATE_SIGMA)),
        water_target_cut=float(settings.agent_get("report.water_target_cut",
                                                   esg_report.DEFAULT_WATER_TARGET_CUT)),
        laundry_target_cut=float(settings.agent_get("report.laundry_target_cut",
                                                     esg_report.DEFAULT_LAUNDRY_TARGET_CUT)),
        waste_benchmark_per_room=float(settings.agent_get("report.waste_benchmark_per_room_kg",
                                                          esg_report.DEFAULT_WASTE_BENCHMARK_PER_ROOM)),
        min_history_days=int(settings.agent_get("report.min_history_days", 30)))

    if dry_run:
        print(f"[dry-run] would draft the ESG report for {result.period_label} "
             f"(electricity {result.last30.kwh_per_room:.2f} kWh/room, "
             f"{result.kwh_delta_pct:+.1f}% vs prior 30 days, {len(result.anomalies)} "
             f"anomaly alert(s)). No business data is written; the run log records a "
             f"dry_run entry.")
        return 1

    item, _created = store.upsert_unique(
        "esg_report", result.period_label,
        payload={"period_label": result.period_label, "kwh_per_room": result.last30.kwh_per_room,
                "cost_per_room": result.last30.cost_per_room, "anomalies": len(result.anomalies)})

    drafted, note = 0, None
    if not item.draft:
        body = esg_report.render_report_md(settings.hotel.name, settings.hotel.currency, result)
        summary = {"period": result.period_label, "kwh_per_room": result.last30.kwh_per_room,
                  "cost_per_room": result.last30.cost_per_room,
                  "findings": [f.title for f in result.findings], "anomalies": len(result.anomalies),
                  "warnings": result.warnings}
        note = get_governance_note(settings, store, item.id, summary, dry_run=dry_run,
                                   provider=provider, fixture_id="governance-note-report")
        if note:
            body += f"\n\n---\n*{note}*"
        recipients = settings.agent_get("report.recipients", []) or (
            [settings.contacts.manager.get("email")] if settings.contacts.manager.get("email") else [])
        subject = f"{settings.hotel.name} - Monthly ESG Report ({result.period_label})"
        store.set_fields(item.id, draft={"subject": subject, "body_md": body, "to": recipients,
                                         "note": note})
        if item.review_status == "new":
            store.transition(item.id, "pending_review", "agent", {"period": result.period_label})
        drafted = 1
        for rec in result.recommendations:
            store_ext.record_recommendation(store, result.period_label, rec.title, rec.metric,
                                            rec.impact)

    alerts = 0
    for a in result.anomalies:
        alert_item, made = store.upsert_unique(
            "engineering_alert", f"{a.metric}:{a.date}",
            payload={"metric": a.metric, "date": a.date, "severity": a.severity,
                    "per_room": a.per_room, "zone": a.zone})
        if not made:
            continue
        unit = "kWh" if a.metric == "electricity" else "m3"
        zone_txt = (f" {a.zone} accounted for {a.zone_share_pct:.0f}% of it."
                   if a.zone else " No per-zone sub-metering is connected.")
        text = (f"{settings.hotel.name}: {a.metric} spike on {a.date} - {a.value:.1f} {unit} "
               f"({a.per_room:.2f} per occupied room, baseline {a.baseline:.2f}), severity "
               f"{a.severity}.{zone_txt}")
        store.set_fields(alert_item.id, draft={"text": text})
        store.transition(alert_item.id, "needs_human", "agent", {"metric": a.metric, "date": a.date})
        alerts += 1

    exported = export_dashboard(settings, result)
    store.set_cursor("esg_report", utcnow())
    record_run(store, "esg", {"period": result.period_label, "anomalies": len(result.anomalies),
                              "alerts": alerts, "exported": exported}, note)
    return drafted + alerts


def one_pass(settings, store, *, provider: str | None, force_report: bool, dry_run: bool) -> dict:
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": 0}
    with Run("esg", settings, store) as run:
        store_ext.migrate(store)
        schedule = settings.agent_get("schedule", {}) or {}

        if is_due(store, "esg_report", cadence_of(schedule, "esg_report", "monthly"), force_report):
            queued = run_esg_report(settings, store, provider=provider, dry_run=dry_run,
                                    source="live")
            stats["processed"] += 1
            stats["drafted"] += 1 if queued else 0
            stats["needs_human"] += max(0, queued - 1) if queued else 0

        reaped = store.reap_stuck_sending()
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))
        stats["dry_run"] = dry_run
        run.stats = dict(stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run if the monthly job is due")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write no business data, even in live mode")
    parser.add_argument("--report", action="store_true", help="force the monthly ESG report")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 86400)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(settings.agent_get("poll_seconds", 86400))
            while True:
                stats = one_pass(settings, store, provider=args.provider,
                                 force_report=args.report, dry_run=args.dry_run)
                print(summary_line(stats, settings.mode))
                time.sleep(poll_seconds)
        stats = one_pass(settings, store, provider=args.provider, force_report=args.report,
                         dry_run=args.dry_run)
        print(summary_line(stats, settings.mode))
        return 0
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
