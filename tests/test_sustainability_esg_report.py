"""tools/esg_report.py - pure engine tests. No settings, no store, no I/O
beyond reading the bundled fixture."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

import esg_report as er

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "inbound"


def _load_daily() -> list[er.DayReading]:
    rows = json.loads((FIXTURES / "sustain_daily.json").read_text(encoding="utf-8"))
    return [er.DayReading(**r) for r in rows]


def _load_zones() -> list[er.ZoneReading]:
    rows = json.loads((FIXTURES / "sustain_zone_daily.json").read_text(encoding="utf-8"))
    return [er.ZoneReading(**r) for r in rows]


def test_deltapct_returns_zero_on_a_zero_base():
    assert er.deltapct(100.0, 0.0) == 0.0
    assert er.deltapct(0.0, 0.0) == 0.0


def test_deltapct_ordinary_case():
    assert er.deltapct(110.0, 100.0) == 10.0
    assert er.deltapct(90.0, 100.0) == -10.0


def test_period_metrics_normalizes_per_occupied_room():
    rows = [er.DayReading(date="2026-01-01", kwh=300, water_m3=40, waste_kg=90,
                          laundry_kg=60, occupied_rooms=30),
           er.DayReading(date="2026-01-02", kwh=300, water_m3=40, waste_kg=90,
                          laundry_kg=60, occupied_rooms=30)]
    m = er.period_metrics(rows, er.DEFAULT_TARIFFS, "1 Jan - 2 Jan")
    assert m.rooms == 60
    assert m.kwh_per_room == 10.0
    assert m.water_per_room == round(80 / 60, 3)
    # cost is priced at the tariffs, never a hardcoded number in the engine's caller
    expected_cost = round(600 * 0.18 + 80 * 3.60 + 180 * 0.19 + 120 * 0.85, 2)
    assert m.cost == expected_cost


def test_period_metrics_zero_rooms_never_divides_by_zero():
    rows = [er.DayReading(date="2026-01-01", kwh=100, occupied_rooms=0)]
    m = er.period_metrics(rows, er.DEFAULT_TARIFFS, "x")
    assert m.kwh_per_room == 0.0
    assert m.cost_per_room == 0.0


def test_scan_anomalies_returns_empty_on_a_flat_series():
    rows = [er.DayReading(date=f"2026-01-{i:02d}", kwh=300, occupied_rooms=30) for i in range(1, 20)]
    assert er.scan_anomalies(rows, "electricity") == []


def test_scan_anomalies_flags_a_genuine_spike_and_names_the_zone():
    rows = [er.DayReading(date=f"2026-01-{i:02d}", kwh=300, water_m3=40, occupied_rooms=30)
           for i in range(1, 20)]
    rows[10] = er.DayReading(date=rows[10].date, kwh=300, water_m3=400, occupied_rooms=30)
    zones = [er.ZoneReading(date=rows[10].date, zone="Floor 3", water_m3=350),
            er.ZoneReading(date=rows[10].date, zone="Floor 1", water_m3=50)]
    flags = er.scan_anomalies(rows, "water", zone_rows=zones)
    assert len(flags) == 1
    assert flags[0].date == rows[10].date
    assert flags[0].zone == "Floor 3"
    assert flags[0].zone_share_pct > 80


def test_scan_anomalies_with_no_zone_data_names_no_zone():
    rows = [er.DayReading(date=f"2026-01-{i:02d}", kwh=300, occupied_rooms=30) for i in range(1, 20)]
    rows[5] = er.DayReading(date=rows[5].date, kwh=900, occupied_rooms=30)
    flags = er.scan_anomalies(rows, "electricity")
    assert flags and flags[0].zone == ""


def test_spike_excess_and_impact_are_never_zero_on_a_low_occupancy_flagged_day():
    """Finding 5: a day can be a genuine per-room outlier (the basis the flag
    itself is raised on) while its ABSOLUTE reading sits at or under the
    property's absolute mean, e.g. a low-occupancy day. `excess` (and the
    priced recommendation built from it) must be computed on the SAME
    per-room baseline that triggered the flag, so a correctly-flagged spike
    can never render as "0.0 kWh above an average day... 0.00 per year"."""
    rows = [er.DayReading(date=f"2026-01-{i:02d}", kwh=300, occupied_rooms=30)
           for i in range(1, 20)]
    # Absolute kwh (280) sits BELOW the flat 300 mean, but occupancy (6) is
    # far below the flat 30 baseline, so the per-room reading is a real
    # outlier (46.7 vs. baseline ~11.9) - exactly the shape the old
    # `val - mean_daily_raw` calculation zeroed out.
    rows[10] = er.DayReading(date=rows[10].date, kwh=280, occupied_rooms=6)
    flags = er.scan_anomalies(rows, "electricity")
    assert len(flags) == 1
    spike = flags[0]
    assert spike.value < statistics.mean(r.kwh for r in rows)   # below the absolute mean
    assert spike.per_room > spike.baseline                       # yet a real per-room outlier
    assert spike.excess > 0                                      # so excess must not zero out

    rec = er.recommend_spike(spike, er.DEFAULT_TARIFFS)
    assert rec.impact > 0
    assert "0.0 kWh above an average day" not in rec.detail


def test_build_report_from_the_bundled_fixture_finds_the_floor_3_water_anomaly():
    rows, zones = _load_daily(), _load_zones()
    result = er.build_report(rows, zones, tariffs=er.DEFAULT_TARIFFS)
    assert result.days == 30
    assert result.warnings == []
    water_anoms = [a for a in result.anomalies if a.metric == "water"]
    assert len(water_anoms) == 1
    assert water_anoms[0].zone == "Floor 3"
    # the roster's own example, demonstrated: "a spike in water use on floor 3"
    titles = " ".join(f.title for f in result.findings)
    assert "water peak" in titles.lower()
    elec_anoms = [a for a in result.anomalies if a.metric == "electricity"]
    assert elec_anoms == []   # a genuinely flat electricity series flags nothing


def test_toggling_the_anomaly_rule_off_swaps_the_recommendation_and_the_finding():
    rows, zones = _load_daily(), _load_zones()
    on = er.build_report(rows, zones, tariffs=er.DEFAULT_TARIFFS, anomaly_enabled=True)
    off = er.build_report(rows, zones, tariffs=er.DEFAULT_TARIFFS, anomaly_enabled=False)
    assert off.anomalies == []
    off_titles = [f.title for f in off.findings]
    assert any("not being surfaced" in t for t in off_titles)
    # recommendation 4 pivots: with an electricity anomaly present it would be the
    # spike recommendation; here there never is one, so both stay on the waste
    # benchmark line - but disabling the rule must never crash or drop a recommendation
    assert len(on.recommendations) == len(off.recommendations) == 4


def test_short_history_warns_instead_of_crashing():
    rows = _load_daily()[:5]
    result = er.build_report(rows, min_history_days=30)
    assert result.days == 5
    assert any("history" in w for w in result.warnings)
    assert any("prior" in w for w in result.warnings)
    # still produces a best-effort report from what little data exists, rather
    # than raising - the warnings are the honesty mechanism, not an empty report
    assert result.prior30.days == 0
    assert result.block3.days == 0


def test_annual_room_nights_uses_the_trailing_average_not_one_month_times_12():
    # 60 rooms/night flat, then 90 rooms/night flat - a single month times 12
    # would give a very different (and wrong) answer depending which month.
    steady = [er.DayReading(date=f"2026-01-{i:02d}", occupied_rooms=60) for i in range(1, 31)]
    busier = [er.DayReading(date=f"2026-02-{i:02d}", occupied_rooms=90) for i in range(1, 29)]
    annual = er.annual_room_nights(steady + busier)
    naive_last_month_times_12 = 90 * 12 * 30  # what the old demo formula would have used
    assert annual != naive_last_month_times_12
    assert 60 * 365 < annual < 90 * 365


def test_fmt_money_uses_the_hotel_currency_not_a_hardcoded_symbol():
    assert er.fmt_money(1234.5, "GBP") == "1,234.50 GBP"
    assert er.fmt_money(1234.5, "NOK") == "1,234.50 NOK"
    assert "EUR" not in er.fmt_money(1234.5, "GBP")


def test_recommendations_are_floored_at_zero_never_a_negative_saving():
    prior = er.PeriodMetrics(label="prior", kwh_per_room=5.0)
    last = er.PeriodMetrics(label="last", kwh_per_room=8.0)   # got worse, not better
    rec = er.recommend_electricity(prior, last, annual_rooms=10000, tariffs=er.DEFAULT_TARIFFS)
    assert rec.impact == 0.0


def test_render_report_md_carries_the_hotel_name_and_period():
    rows, zones = _load_daily(), _load_zones()
    result = er.build_report(rows, zones, tariffs=er.DEFAULT_TARIFFS)
    body = er.render_report_md("Hotel Aurora", "EUR", result)
    assert "Hotel Aurora" in body
    assert result.period_label in body
    assert "Method" in body
    assert "Estimated emissions" in body


def test_export_dashboard_rows_shape():
    rows, zones = _load_daily(), _load_zones()
    result = er.build_report(rows, zones, tariffs=er.DEFAULT_TARIFFS)
    out = er.export_dashboard_rows("Hotel Aurora", result)
    assert len(out) == 1
    assert len(out[0]) == len(er.DASHBOARD_HEADER)
    assert out[0][0] == "Hotel Aurora"
