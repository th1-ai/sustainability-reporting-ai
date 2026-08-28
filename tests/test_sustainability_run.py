"""Integration tests for tools/run.py against an isolated settings + store.

Uses `isolated_settings`/`store_at` from conftest.py so nothing here reads a
hotel's own config/hotel.yaml or config/agent.yaml.
"""

from __future__ import annotations

import store_ext
from core.review import WriteBlocked, assert_write_allowed, approve
from core.store import Store as CoreStore
from run import get_governance_note, one_pass, run_esg_report


def _seeded(isolated_settings, store_at, fixtures_dir):
    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    store_ext.seed_fixtures(store, fixtures_dir)
    return settings, store


def test_full_demo_loop_drafts_a_report_and_one_engineering_alert(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    queued = run_esg_report(settings, store, provider=None, dry_run=False)
    assert queued == 2   # 1 esg_report + 1 engineering_alert (the Floor 3 water spike)

    kinds = {i.kind for i in store.list_items(limit=100)}
    assert {"esg_report", "engineering_alert"} <= kinds

    report = store.list_items(kind="esg_report", limit=1)[0]
    assert report.review_status == "pending_review"
    assert "Method" in report.draft["body_md"]

    alert = store.list_items(kind="engineering_alert", limit=1)[0]
    assert alert.review_status == "needs_human"
    assert "Floor 3" in alert.draft["text"]


def test_shadow_mode_blocks_the_send_even_once_approved(isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_esg_report(settings, store, provider=None, dry_run=False)
    item = store.list_items(kind="esg_report", limit=1)[0]
    approve(store, item.id)
    item = store.get_item(item.id)
    try:
        assert_write_allowed(settings, "send_email", item)
        assert False, "expected WriteBlocked in shadow mode"
    except WriteBlocked as exc:
        assert "shadow" in str(exc)


def test_rerunning_the_same_period_does_not_redraft_or_duplicate_the_alert(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    first = run_esg_report(settings, store, provider=None, dry_run=False)
    second = run_esg_report(settings, store, provider=None, dry_run=False)
    assert first == 2
    assert second == 0   # same period, same anomalies - nothing new to queue
    assert len(store.list_items(kind="esg_report", limit=100)) == 1
    assert len(store.list_items(kind="engineering_alert", limit=100)) == 1


def test_dry_run_writes_no_business_rows(isolated_settings, store_at, fixtures_dir, tmp_path):
    settings = isolated_settings(provider="mock", mode="shadow", dry_run=True)
    store = store_at(settings)
    store_ext.seed_fixtures(store, fixtures_dir)
    queued = run_esg_report(settings, store, provider=None, dry_run=True)
    assert queued == 1   # a printed intent, not an item
    assert store.list_items(limit=100) == []
    assert store.counts() == {}


def test_dry_run_twice_on_fresh_fixtures_never_conflicts(isolated_settings, store_at, fixtures_dir):
    settings = isolated_settings(provider="mock", mode="shadow", dry_run=True)
    store = store_at(settings)
    store_ext.seed_fixtures(store, fixtures_dir)
    run_esg_report(settings, store, provider=None, dry_run=True)
    run_esg_report(settings, store, provider=None, dry_run=True)   # must not raise
    assert store.list_items(limit=100) == []


def test_governance_note_is_skipped_not_paused_on_interactive(
        isolated_settings, store_at, fixtures_dir):
    settings = isolated_settings(provider="interactive", mode="shadow")
    store = store_at(settings)
    note = get_governance_note(settings, store, "item-1", {"period": "x"}, dry_run=False,
                               provider="interactive", fixture_id="governance-note-report")
    assert note is None   # never raises LLMPendingInteractive - see docs/how-it-works.md


def test_engineering_alert_send_notifies_staff_in_live_mode(
        isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    run_esg_report(settings, store, provider=None, dry_run=False)
    alert = store.list_items(kind="engineering_alert", limit=1)[0]
    approve(store, alert.id)

    from core.adapters import get_messaging
    live_settings = isolated_settings(provider="mock", mode="live")
    claimed = store.claim_for_send(limit=5)
    assert len(claimed) == 1
    messaging = get_messaging(live_settings)
    result = messaging.notify_staff((claimed[0].draft or {}).get("text", ""), item=claimed[0])
    assert result["ok"] is True
    store.mark_sent(claimed[0].id, result.get("message_id"))
    assert store.get_item(claimed[0].id).review_status == "sent"


def test_no_meter_data_returns_zero_and_does_not_crash(isolated_settings, store_at):
    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    assert run_esg_report(settings, store, provider=None, dry_run=False) == 0
    assert store.list_items(limit=100) == []


def test_one_pass_reports_a_non_zero_summary_after_seeding(isolated_settings, store_at, fixtures_dir):
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    stats = one_pass(settings, store, provider=None, force_report=True, dry_run=False)
    assert stats["processed"] == 1
    assert stats["drafted"] == 1
    assert stats["needs_human"] == 1
    assert stats["sent"] == 0


def _plant_decoy_csvs(settings) -> None:
    """A hotel's own real export, sitting exactly where `data/imports/` lives
    for THIS settings object - never the real repo's data/imports/ (Finding 1:
    a hermetic test must never see a hotel's real files either way)."""
    imports_dir = settings.root / "data" / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    (imports_dir / "sustain_daily.csv").write_text(
        "date,kwh,water_m3,waste_kg,laundry_kg,occupied_rooms\n"
        "2099-01-01,999999,999999,999999,999999,1\n", encoding="utf-8")
    (imports_dir / "sustain_zone_daily.csv").write_text(
        "date,zone,kwh,water_m3\n2099-01-01,Decoy Zone,999999,999999\n", encoding="utf-8")


def test_demo_source_never_reads_a_hotels_real_csv_imports(
        isolated_settings, store_at, fixtures_dir):
    """Finding 3 + Finding 4: `make demo` must show the bundled fixtures ONLY,
    even once a hotel's own real data/imports/*.csv exist on disk."""
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    _plant_decoy_csvs(settings)

    queued = run_esg_report(settings, store, provider=None, dry_run=False, source="demo")
    assert queued == 2   # identical to the ordinary fixture-only demo loop

    rows = store_ext.load_sustain_daily(store)
    assert all(r.date != "2099-01-01" for r in rows), \
        "demo must never import a hotel's real sustain_daily.csv"
    zone_rows = store_ext.load_sustain_zone_daily(store)
    assert all(z.zone != "Decoy Zone" for z in zone_rows), \
        "demo must never import a hotel's real sustain_zone_daily.csv"

    report = store.list_items(kind="esg_report", limit=1)[0]
    assert "999999" not in report.draft["body_md"]


def test_live_source_still_imports_a_hotels_real_csv(isolated_settings, store_at, fixtures_dir):
    """The live path (`source="live"`, the default `tools/run.py` uses) must
    keep reading data/imports/*.csv - only `demo` skips it."""
    settings, store = _seeded(isolated_settings, store_at, fixtures_dir)
    _plant_decoy_csvs(settings)

    run_esg_report(settings, store, provider=None, dry_run=False)   # source="live" default

    rows = store_ext.load_sustain_daily(store)
    assert any(r.date == "2099-01-01" for r in rows)
