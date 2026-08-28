#!/usr/bin/env python3
"""tools/doctor.py - is Sustainability Reporting AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
checks specific to this agent: the prompt + schema files, the meter CSV
sources (checked with the same loaders `tools/run.py` calls before every
pass, against a throwaway in-memory store - a PASS means the file was
actually parsed into rows, not just that it exists), and where the report
and the engineering alerts are configured to go. Exits 0 when everything
passed, 1 when a FAIL line needs fixing. Never a traceback: a config error
is shown as a FAIL row like any other.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402

import store_ext  # noqa: E402


def check_prompts() -> Check:
    missing = [p for p in ("prompts/governance-note.md", "prompts/schemas/governance-note.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "governance-note.md + schema present")


def _probe_store():
    """A throwaway, in-memory store - never the hotel's own `data/agent.db` -
    so doctor can run the exact CSV loaders `tools/run.py` calls before every
    pass without touching real state."""
    from core.store import Store
    probe = Store(None, path=":memory:")
    store_ext.migrate(probe)
    return probe


def check_data_sources() -> list[Check]:
    """The same paths, and the same loaders, `tools/run.py` uses before every
    pass - see docs/integrations.md. A PASS here means the file was actually
    parsed into rows, not just that it exists."""
    imports = REPO_ROOT / "data" / "imports"
    checks = []
    probe = _probe_store()
    try:
        daily_path = imports / "sustain_daily.csv"
        n_daily = store_ext.import_sustain_daily_csv(probe, daily_path)
        if not daily_path.exists():
            checks.append(Check("data/imports/sustain_daily.csv", WARN,
                                "not found - the report will say 'nothing to report'",
                                "Export your meter reads and save as "
                                "data/imports/sustain_daily.csv - see docs/integrations.md. "
                                "`make demo` works without it (uses fixtures instead)."))
        elif n_daily == 0:
            checks.append(Check("data/imports/sustain_daily.csv", WARN,
                                "found, but the loader could not read a single row",
                                "Check the column headers match docs/integrations.md."))
        else:
            checks.append(Check("data/imports/sustain_daily.csv", PASS,
                                f"found - {n_daily} row(s) the loader will import"))

        zone_path = imports / "sustain_zone_daily.csv"
        n_zone = store_ext.import_sustain_zone_daily_csv(probe, zone_path)
        if not zone_path.exists():
            checks.append(Check("data/imports/sustain_zone_daily.csv", WARN,
                                "not found - anomaly findings will report at the property "
                                "level, not by zone/floor",
                                "Optional. Add it once you have per-zone sub-metering - see "
                                "docs/integrations.md."))
        else:
            checks.append(Check("data/imports/sustain_zone_daily.csv",
                                PASS if n_zone else WARN,
                                f"found - {n_zone} row(s) the loader will import"))
    finally:
        probe.close()
    return checks


def check_recipients(settings: Settings) -> Check:
    recipients = settings.agent_get("report.recipients", []) or []
    manager = (settings.contacts.manager or {}).get("email", "")
    if recipients or manager:
        return Check("ESG report recipients", PASS,
                     ", ".join(recipients) if recipients else manager)
    return Check("ESG report recipients", WARN, "no recipients configured",
                 "Set report.recipients in config/agent.yaml, or contacts.manager.email "
                 "in config/hotel.yaml - the report will queue but `send` will fail without one.")


def check_anomaly_rule(settings: Settings) -> Check:
    enabled = bool(settings.agent_get("anomaly.rules.esg-anomaly", True))
    if not enabled:
        return Check("esg-anomaly rule", WARN, "off - single-day spikes will not be surfaced",
                     "Set anomaly.rules.esg-anomaly: true in config/agent.yaml to turn it back on.")
    return Check("esg-anomaly rule", PASS,
                 f"on - flag_sigma={settings.agent_get('anomaly.flag_sigma', 2)}, "
                 f"escalate_sigma={settings.agent_get('anomaly.escalate_sigma', 3)}")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Sustainability Reporting AI - doctor")

    checks = run_checks(settings, extra=[check_recipients, check_anomaly_rule])
    checks.append(check_prompts())
    checks += check_data_sources()
    return print_table(checks, title="Sustainability Reporting AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
