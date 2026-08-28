"""tools/esg_report.py - the Monthly ESG Report engine ("The Ranger"). Pure functions only.

No I/O here: everything takes plain data in and returns dataclasses out, so it
is trivial to test and safe to call from tools/run.py, tools/demo.py or a test.
Every number is arithmetic over `DayReading` rows - the LLM never touches one,
except the one governance note appended after this engine has already decided
everything. See docs/how-it-works.md for the design and every place this
build differs from the demo it was extracted from ("Design decisions").
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

DEFAULT_FLAG_SIGMA = 2.0
DEFAULT_ESCALATE_SIGMA = 3.0
DEFAULT_WATER_TARGET_CUT = 0.06
DEFAULT_LAUNDRY_TARGET_CUT = 0.05
DEFAULT_WASTE_BENCHMARK_PER_ROOM = 4.0

DEFAULT_TARIFFS = {
    "elec_per_kwh": 0.18, "water_per_m3": 3.60, "waste_per_kg": 0.19,
    "laundry_per_kg": 0.85, "grid_kgco2_per_kwh": 0.233,
}


@dataclass
class DayReading:
    """One day's meter reads for the whole property."""

    date: str
    kwh: float = 0.0
    water_m3: float = 0.0
    waste_kg: float = 0.0
    laundry_kg: float = 0.0
    occupied_rooms: int = 0


@dataclass
class ZoneReading:
    """Optional per-zone sub-metering - see docs/how-it-works.md 'Design decisions' #2."""

    date: str
    zone: str
    kwh: float = 0.0
    water_m3: float = 0.0


@dataclass
class PeriodMetrics:
    label: str
    days: int = 0
    kwh: float = 0.0
    water_m3: float = 0.0
    waste_kg: float = 0.0
    laundry_kg: float = 0.0
    rooms: int = 0
    kwh_per_room: float = 0.0
    water_per_room: float = 0.0
    waste_per_room: float = 0.0
    laundry_per_room: float = 0.0
    cost: float = 0.0
    cost_per_room: float = 0.0


@dataclass
class Anomaly:
    metric: str            # "electricity" | "water"
    date: str
    value: float            # raw property-wide reading that day
    per_room: float
    occupied_rooms: int
    baseline: float
    sigma: float
    severity: str            # medium | high
    excess: float            # raw units above what the per-room baseline implies for
                              # THIS day's own occupancy - same basis as the flag itself,
                              # so a flagged day can never price out at zero (Finding 5)
    zone: str = ""           # named only when sustain_zone_daily has rows for this date
    zone_share_pct: float = 0.0


@dataclass
class Finding:
    title: str
    tone: str    # success | warn | info
    text: str


@dataclass
class Recommendation:
    title: str
    detail: str
    impact: float            # annual saving, in the property's own currency - format at render time
    impact_label: str        # units text only, e.g. "per year" - never bakes in a currency
    metric: str = ""


@dataclass
class EsgReportResult:
    period_label: str
    prior_period_label: str
    block3_label: str
    days: int
    last30: PeriodMetrics
    prior30: PeriodMetrics
    block3: PeriodMetrics
    kwh_delta_pct: float
    water_delta_pct: float
    waste_delta_pct: float
    laundry_delta_pct: float
    rooms_delta_pct: float
    anomalies: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    recommendations: list = field(default_factory=list)
    co2e_last30_kg: float = 0.0
    co2e_per_room_kg: float = 0.0
    annual_room_nights: float = 0.0
    method_text: str = ""
    warnings: list = field(default_factory=list)


def safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def deltapct(now: float, before: float) -> float:
    """Percent change. Returns 0 rather than NaN/Infinity on a zero base."""
    if not before:
        return 0.0
    return round((now - before) / before * 100, 1)


def fmt_money(value: float, currency: str) -> str:
    """The one place a figure gets a currency code. Never hard-code EUR/USD elsewhere."""
    return f"{value:,.2f} {currency}"


def period_label(rows: list[DayReading]) -> str:
    if not rows:
        return ""
    start, end = rows[0].date, rows[-1].date

    def short(d: str) -> str:
        from datetime import date as _date
        try:
            dt = _date.fromisoformat(d)
            return f"{dt.day} {dt.strftime('%b')}"
        except ValueError:
            return d
    return f"{short(start)} - {short(end)}"


def period_metrics(rows: list[DayReading], tariffs: dict, label: str) -> PeriodMetrics:
    """Sum a 30-day block and restate every figure per occupied room-night -
    the central design rule: raw consumption tracks how full the hotel was, so
    only the per-room figure is comparable across periods or properties."""
    days = len(rows)
    kwh = round(sum(r.kwh for r in rows), 2)
    water = round(sum(r.water_m3 for r in rows), 2)
    waste = round(sum(r.waste_kg for r in rows), 2)
    laundry = round(sum(r.laundry_kg for r in rows), 2)
    rooms = sum(r.occupied_rooms for r in rows)
    cost = round(kwh * tariffs["elec_per_kwh"] + water * tariffs["water_per_m3"]
                + waste * tariffs["waste_per_kg"] + laundry * tariffs["laundry_per_kg"], 2)
    return PeriodMetrics(
        label=label, days=days, kwh=kwh, water_m3=water, waste_kg=waste, laundry_kg=laundry,
        rooms=rooms, kwh_per_room=round(safe_div(kwh, rooms), 2),
        water_per_room=round(safe_div(water, rooms), 3), waste_per_room=round(safe_div(waste, rooms), 2),
        laundry_per_room=round(safe_div(laundry, rooms), 2), cost=cost,
        cost_per_room=round(safe_div(cost, rooms), 2))


def annual_room_nights(rows: list[DayReading]) -> float:
    """Average daily occupied rooms across the whole window, annualised - not one
    month's room-nights times 12. See docs/how-it-works.md 'Design decisions' #6."""
    if not rows:
        return 0.0
    return sum(r.occupied_rooms for r in rows) / len(rows) * 365


# --------------------------------------------------------------------------
# anomaly detection - baseline + sigma, not "always the highest day"
# --------------------------------------------------------------------------
def _named_zone(zone_rows: list[ZoneReading] | None, date: str, metric: str) -> tuple[str, float]:
    """Which zone drove `metric` on `date`, if sustain_zone_daily has rows for it."""
    if not zone_rows:
        return "", 0.0
    field_name = "kwh" if metric == "electricity" else "water_m3"
    day_rows = [z for z in zone_rows if z.date == date]
    if not day_rows:
        return "", 0.0
    total = sum(getattr(z, field_name) for z in day_rows)
    if total <= 0:
        return "", 0.0
    best = max(day_rows, key=lambda z: getattr(z, field_name))
    return best.zone, round(getattr(best, field_name) / total * 100, 1)


def scan_anomalies(rows: list[DayReading], metric: str, *, flag_sigma: float = DEFAULT_FLAG_SIGMA,
                   escalate_sigma: float = DEFAULT_ESCALATE_SIGMA,
                   zone_rows: list[ZoneReading] | None = None) -> list[Anomaly]:
    """Flag days whose per-occupied-room `metric` reading exceeds
    mean + escalate_sigma * (population stdev) across `rows`. Returns an empty
    list when nothing crosses the threshold - a flat series produces genuinely
    no anomaly, never a forced maximum. See docs/how-it-works.md 'Design
    decisions' #3-4 (this runs once for electricity, once for water)."""
    field_name = "kwh" if metric == "electricity" else "water_m3"
    per_room = [(r, getattr(r, field_name), safe_div(getattr(r, field_name), r.occupied_rooms))
               for r in rows]
    values = [pr for _, _, pr in per_room]
    if len(values) < 2:
        return []
    baseline = statistics.mean(values)
    sigma = statistics.pstdev(values)
    threshold = baseline + flag_sigma * sigma
    escalate_at = baseline + escalate_sigma * sigma
    flags: list[Anomaly] = []
    for r, val, pr in per_room:
        if pr <= threshold:
            continue
        severity = "high" if pr >= escalate_at else "medium"
        zone, zone_share = _named_zone(zone_rows, r.date, metric)
        # Excess must be priced on the SAME baseline that triggered the flag
        # (per-room), then converted back to raw units at THIS day's own
        # occupancy - never against the property-wide absolute mean, which
        # can sit at or above `val` on a low-occupancy spike day and silently
        # zero out a genuine, correctly-flagged anomaly (Finding 5).
        excess = max(0.0, pr - baseline) * r.occupied_rooms
        flags.append(Anomaly(
            metric=metric, date=r.date, value=round(val, 2), per_room=round(pr, 3),
            occupied_rooms=r.occupied_rooms, baseline=round(baseline, 3), sigma=round(sigma, 3),
            severity=severity, excess=round(excess, 2),
            zone=zone, zone_share_pct=zone_share))
    flags.sort(key=lambda a: -a.per_room)
    return flags


# --------------------------------------------------------------------------
# findings - five callouts, each naming the control that makes it a claim
# --------------------------------------------------------------------------
def electricity_finding(last30: PeriodMetrics, prior30: PeriodMetrics, rooms_delta_pct: float,
                        kwh_delta_pct: float) -> Finding:
    improving = kwh_delta_pct <= 0
    title = ("Electricity intensity is genuinely down" if improving
            else "Electricity intensity is moving the wrong way")
    text = (f"{last30.kwh_per_room:.2f} kWh per occupied room this period against "
           f"{prior30.kwh_per_room:.2f} the prior 30 days, a {abs(kwh_delta_pct):.1f}% "
           f"{'cut' if improving else 'rise'} on only {abs(rooms_delta_pct):.1f}% movement in "
           f"room-nights - the room-nights control rules out 'the hotel was just "
           f"{'quieter' if improving else 'busier'}', so this is a real change in how much "
           f"electricity each stay uses.")
    return Finding(title=title, tone=("success" if improving else "warn"), text=text)


def water_finding(last30: PeriodMetrics, prior30: PeriodMetrics, block3: PeriodMetrics,
                  water_delta_pct: float) -> Finding:
    if abs(water_delta_pct) < 3.0:
        text = (f"{last30.water_per_room:.3f} m3 per occupied room this period, against "
               f"{prior30.water_per_room:.3f} the month before and {block3.water_per_room:.3f} "
               f"the month before that - flat across the whole quarter. Whatever changed on the "
               f"electricity side has no water equivalent yet.")
        return Finding("Water is the one that has not moved", "warn", text)
    improving = water_delta_pct <= 0
    title = "Water is moving with electricity" if improving else "Water is moving the wrong way"
    text = (f"{last30.water_per_room:.3f} m3 per occupied room this period against "
           f"{prior30.water_per_room:.3f} the prior 30 days, a {abs(water_delta_pct):.1f}% "
           f"{'cut' if improving else 'rise'}.")
    return Finding(title, ("success" if improving else "warn"), text)


def anomaly_finding(metric_label: str, anomalies: list[Anomaly], enabled: bool) -> Finding:
    unit = "kWh" if metric_label == "electricity" else "m3"
    if not enabled:
        return Finding(
            f"Single-day {metric_label} spikes are not being surfaced", "info",
            f"The 'Utility anomaly alerts' rule is switched off, so single-day {metric_label} "
            f"spikes are not surfaced in this report. Monthly totals hide them.")
    if not anomalies:
        return Finding(
            f"No single-day {metric_label} spikes this period", "info",
            f"Every day's {metric_label} reading stayed within the property's usual range this "
            f"period - none crossed the baseline.")
    a = anomalies[0]
    zone_txt = (f" {a.zone} accounted for {a.zone_share_pct:.0f}% of that day's total."
               if a.zone else " No per-zone sub-metering is connected, so this can only be "
               "reported at the property level - see docs/integrations.md.")
    text = (f"{a.date} used {a.value:.1f} {unit} on {a.occupied_rooms} occupied rooms - "
           f"{a.per_room:.2f} {unit} per room against a baseline of {a.baseline:.2f}.{zone_txt} "
           f"Occupancy does not explain it, so plant does.")
    return Finding(f"One day accounts for an outsized share of the {metric_label} peak", "warn", text)


def laundry_finding(last30: PeriodMetrics, prior30: PeriodMetrics, laundry_delta_pct: float) -> Finding:
    improving = laundry_delta_pct <= 0
    title = "Laundry is tracking with occupancy" if abs(laundry_delta_pct) < 3.0 else (
        "Laundry per stay is falling" if improving else "Laundry per stay is rising")
    text = (f"{last30.laundry_per_room:.2f} kg per occupied room this period against "
           f"{prior30.laundry_per_room:.2f} the prior 30 days, a {abs(laundry_delta_pct):.1f}% "
           f"{'cut' if improving else 'rise'}.")
    return Finding(title, ("success" if improving else "warn"), text)


# --------------------------------------------------------------------------
# recommendations - each priced at the property's own tariffs, each floored
# at zero so a regression never renders as a "saving"
# --------------------------------------------------------------------------
def recommend_electricity(prior30: PeriodMetrics, last30: PeriodMetrics, annual_rooms: float,
                          tariffs: dict) -> Recommendation:
    saved = max(0.0, prior30.kwh_per_room - last30.kwh_per_room)
    impact = round(saved * annual_rooms * tariffs["elec_per_kwh"], 2)
    return Recommendation(
        "Lock in the new plant schedule as the standing setpoint",
        "Write the current HVAC and lighting schedule into the BMS as the default rather "
        "than leaving it as a trial, so it survives the next shift change.",
        impact, "per year", metric="electricity")


def recommend_water(last30: PeriodMetrics, annual_rooms: float, tariffs: dict,
                    target_cut: float = DEFAULT_WATER_TARGET_CUT) -> Recommendation:
    impact = round(last30.water_per_room * target_cut * annual_rooms * tariffs["water_per_m3"], 2)
    return Recommendation(
        "Give water the same treatment electricity just had",
        f"A linen-reuse prompt plus aerators on guest-room taps is a realistic "
        f"{target_cut * 100:.0f}% cut.",
        impact, "per year", metric="water")


def recommend_laundry(last30: PeriodMetrics, annual_rooms: float, tariffs: dict,
                      target_cut: float = DEFAULT_LAUNDRY_TARGET_CUT) -> Recommendation:
    impact = round(last30.laundry_per_room * target_cut * annual_rooms * tariffs["laundry_per_kg"], 2)
    return Recommendation(
        "Move to a tiered linen-change program",
        f"Ask returning guests once, not every day, whether they want fresh linen - a "
        f"realistic {target_cut * 100:.0f}% cut in laundry volume per stay.",
        impact, "per year", metric="laundry")


def recommend_spike(anomaly: Anomaly, tariffs: dict) -> Recommendation:
    impact = round(anomaly.excess * 12 * tariffs["elec_per_kwh"], 2)
    return Recommendation(
        "Find what ran on the spike days",
        f"{anomaly.date} used {anomaly.value:.1f} kWh on {anomaly.occupied_rooms} occupied "
        f"rooms - {anomaly.excess:.1f} kWh above an average day. One plant item running out "
        f"of schedule is the usual cause. If it recurs monthly at that size it is worth chasing.",
        impact, "per year if it recurs monthly", metric="electricity")


def recommend_waste_benchmark(last30: PeriodMetrics, annual_rooms: float, tariffs: dict,
                              benchmark: float = DEFAULT_WASTE_BENCHMARK_PER_ROOM) -> Recommendation:
    impact = round(max(0.0, last30.waste_per_room - benchmark) * annual_rooms
                  * tariffs["waste_per_kg"], 2)
    return Recommendation(
        "Take waste to the group benchmark",
        f"{last30.waste_per_room:.2f} kg per occupied room against a group benchmark of "
        f"{benchmark:.1f} kg. Closing that gap is the next lever once electricity is under "
        f"control.",
        impact, "per year", metric="waste")


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------
def build_report(rows: list[DayReading], zone_rows: list[ZoneReading] | None = None, *,
                 tariffs: dict | None = None, anomaly_enabled: bool = True,
                 flag_sigma: float = DEFAULT_FLAG_SIGMA, escalate_sigma: float = DEFAULT_ESCALATE_SIGMA,
                 water_target_cut: float = DEFAULT_WATER_TARGET_CUT,
                 laundry_target_cut: float = DEFAULT_LAUNDRY_TARGET_CUT,
                 waste_benchmark_per_room: float = DEFAULT_WASTE_BENCHMARK_PER_ROOM,
                 min_history_days: int = 30) -> EsgReportResult:
    """Build the full ESG report from ascending daily rows (oldest first).

    Uses the last 90 rows in three 30-day blocks: `last30 = rows[-30:]`,
    `prior30 = rows[-60:-30]`, `block3 = rows[-90:-60]` (the 30 days before
    that - "61-90 days ago", not a 90-day window). With fewer than 90 rows
    the older blocks are whatever is left, possibly empty, and a warning is
    attached rather than the report raising - see docs/how-it-works.md
    'Design decisions'.
    """
    tariffs = {**DEFAULT_TARIFFS, **(tariffs or {})}
    warnings: list[str] = []
    if len(rows) < min_history_days:
        warnings.append(
            f"only {len(rows)} day(s) of meter history - the report below is partial. "
            f"Load at least {min_history_days} days into sustain_daily for a full month.")
    last30, prior30, block3 = rows[-30:], rows[-60:-30], rows[-90:-60]
    if len(prior30) < len(last30):
        warnings.append(
            f"only {len(prior30)} prior day(s) available - month-over-month deltas compare "
            f"against a short or empty prior period.")

    m_last30 = period_metrics(last30, tariffs, period_label(last30))
    m_prior30 = period_metrics(prior30, tariffs, period_label(prior30))
    m_block3 = period_metrics(block3, tariffs, period_label(block3))

    kwh_delta = deltapct(m_last30.kwh_per_room, m_prior30.kwh_per_room)
    water_delta = deltapct(m_last30.water_per_room, m_prior30.water_per_room)
    waste_delta = deltapct(m_last30.waste_per_room, m_prior30.waste_per_room)
    laundry_delta = deltapct(m_last30.laundry_per_room, m_prior30.laundry_per_room)
    rooms_delta = deltapct(m_last30.rooms, m_prior30.rooms)

    scanned = block3 + prior30 + last30
    elec_anomalies = (scan_anomalies(scanned, "electricity", flag_sigma=flag_sigma,
                                     escalate_sigma=escalate_sigma, zone_rows=zone_rows)
                      if anomaly_enabled else [])
    water_anomalies = (scan_anomalies(scanned, "water", flag_sigma=flag_sigma,
                                      escalate_sigma=escalate_sigma, zone_rows=zone_rows)
                       if anomaly_enabled else [])
    annual_rooms = annual_room_nights(scanned)

    findings: list[Finding] = []
    recommendations: list[Recommendation] = []
    if m_last30.days and m_last30.rooms:
        findings = [
            electricity_finding(m_last30, m_prior30, rooms_delta, kwh_delta),
            water_finding(m_last30, m_prior30, m_block3, water_delta),
            anomaly_finding("electricity", elec_anomalies, anomaly_enabled),
            anomaly_finding("water", water_anomalies, anomaly_enabled),
            laundry_finding(m_last30, m_prior30, laundry_delta),
        ]
        recommendations = [
            recommend_electricity(m_prior30, m_last30, annual_rooms, tariffs),
            recommend_water(m_last30, annual_rooms, tariffs, water_target_cut),
            recommend_laundry(m_last30, annual_rooms, tariffs, laundry_target_cut),
            (recommend_spike(elec_anomalies[0], tariffs) if elec_anomalies else
             recommend_waste_benchmark(m_last30, annual_rooms, tariffs, waste_benchmark_per_room)),
        ]

    co2e_last30 = round(m_last30.kwh * tariffs["grid_kgco2_per_kwh"], 1)
    co2e_per_room = round(safe_div(co2e_last30, m_last30.rooms), 3)

    method_text = (
        f"Consumption is taken from the property's meter reads for the {m_last30.days}-day "
        f"window and divided by occupied room-nights from the PMS export for the same dates. "
        f"Cost is priced at {tariffs['elec_per_kwh']:g}/kWh, {tariffs['water_per_m3']:g}/m3, "
        f"{tariffs['waste_per_kg']:g}/kg waste and {tariffs['laundry_per_kg']:g}/kg laundry, in "
        f"the property's own currency. No estimates or interpolation were used for consumption "
        f"or cost; every one of those figures is reproducible from the meter table. The "
        f"estimated emissions figure below is the one exception - it applies a published grid "
        f"factor, not a meter reading.")

    return EsgReportResult(
        period_label=m_last30.label, prior_period_label=m_prior30.label, block3_label=m_block3.label,
        days=m_last30.days, last30=m_last30, prior30=m_prior30, block3=m_block3,
        kwh_delta_pct=kwh_delta, water_delta_pct=water_delta, waste_delta_pct=waste_delta,
        laundry_delta_pct=laundry_delta, rooms_delta_pct=rooms_delta,
        anomalies=elec_anomalies + water_anomalies, findings=findings, recommendations=recommendations,
        co2e_last30_kg=co2e_last30, co2e_per_room_kg=co2e_per_room,
        annual_room_nights=round(annual_rooms, 1), method_text=method_text, warnings=warnings)


def render_report_md(hotel_name: str, currency: str, result: EsgReportResult) -> str:
    """Plain-markdown report body a human can read and send as-is."""
    lines = [f"# {hotel_name} - Monthly ESG Report", "",
            f"**{result.period_label}** (vs prior 30 days {result.prior_period_label})", ""]
    for w in result.warnings:
        lines.append(f"> Note: {w}")
    if result.warnings:
        lines.append("")
    lines += [
        "Consumption is taken from the meter table and divided by occupied room-nights for "
        "the same dates - see Method below.", "",
        "## KPI tiles",
        f"- Electricity per occupied room: {result.last30.kwh_per_room:.2f} kWh "
        f"({result.kwh_delta_pct:+.1f}% vs prior 30 days)",
        f"- Water per occupied room: {result.last30.water_per_room:.3f} m3 "
        f"({result.water_delta_pct:+.1f}%)",
        f"- Waste per occupied room: {result.last30.waste_per_room:.2f} kg "
        f"({result.waste_delta_pct:+.1f}%)",
        f"- Laundry per occupied room: {result.last30.laundry_per_room:.2f} kg "
        f"({result.laundry_delta_pct:+.1f}%)",
        f"- Utility cost per occupied room: {fmt_money(result.last30.cost_per_room, currency)}",
        "", "## Metered consumption",
        "| Metric | Last 30 | Prior 30 | Change | Per occupied room |",
        "|---|---|---|---|---|",
        f"| Electricity (kWh) | {result.last30.kwh:,.0f} | {result.prior30.kwh:,.0f} | "
        f"{result.kwh_delta_pct:+.1f}% | {result.last30.kwh_per_room:.2f} kWh |",
        f"| Water (m3) | {result.last30.water_m3:,.1f} | {result.prior30.water_m3:,.1f} | "
        f"{result.water_delta_pct:+.1f}% | {result.last30.water_per_room:.3f} m3 |",
        f"| Waste (kg) | {result.last30.waste_kg:,.0f} | {result.prior30.waste_kg:,.0f} | "
        f"{result.waste_delta_pct:+.1f}% | {result.last30.waste_per_room:.2f} kg |",
        f"| Laundry (kg) | {result.last30.laundry_kg:,.0f} | {result.prior30.laundry_kg:,.0f} | "
        f"{result.laundry_delta_pct:+.1f}% | {result.last30.laundry_per_room:.2f} kg |",
        f"| Occupied room-nights | {result.last30.rooms} | {result.prior30.rooms} | "
        f"{result.rooms_delta_pct:+.1f}% | - |",
        f"| Utility cost | {fmt_money(result.last30.cost, currency)} | "
        f"{fmt_money(result.prior30.cost, currency)} | - | "
        f"{fmt_money(result.last30.cost_per_room, currency)} |",
        "", "## Electricity per occupied room, by 30-day block",
        f"- {result.block3_label or 'n/a'} (61-90 days ago): {result.block3.kwh_per_room:.2f} kWh",
        f"- {result.prior_period_label or 'n/a'} (31-60 days ago): {result.prior30.kwh_per_room:.2f} kWh",
        f"- {result.period_label or 'n/a'} (last 30 days): {result.last30.kwh_per_room:.2f} kWh",
        "", "## What stood out"]
    for f in result.findings:
        lines.append(f"- **{f.title}.** {f.text}")
    lines += ["", "## Recommendations", "| Action | Why | Annual impact |", "|---|---|---|"]
    for r in result.recommendations:
        lines.append(f"| {r.title} | {r.detail} | {fmt_money(r.impact, currency)} {r.impact_label} |")
    lines += [
        "", "## Estimated emissions (Scope 2, location-based)",
        f"{result.co2e_last30_kg:,.0f} kg CO2e this period, {result.co2e_per_room_kg:.2f} kg "
        f"CO2e per occupied room. Estimated from metered electricity and a grid factor - not a "
        f"meter reading. See Method.",
        "", "## Method", result.method_text]
    return "\n".join(lines)


def export_dashboard_rows(hotel_name: str, result: EsgReportResult) -> list[list]:
    """One row for the per-property consumption dashboard export (CSV/Sheets).

    Columns: hotel, period, kWh/room, m3/room, waste kg/room, laundry kg/room,
    cost/room, kg CO2e/room, kWh delta %, water delta %.
    """
    return [[hotel_name, result.period_label, result.last30.kwh_per_room,
            result.last30.water_per_room, result.last30.waste_per_room,
            result.last30.laundry_per_room, result.last30.cost_per_room,
            result.co2e_per_room_kg, result.kwh_delta_pct, result.water_delta_pct]]


DASHBOARD_HEADER = ["hotel", "period", "kwh_per_room", "water_m3_per_room", "waste_kg_per_room",
                    "laundry_kg_per_room", "cost_per_room", "co2e_kg_per_room",
                    "kwh_delta_pct", "water_delta_pct"]
