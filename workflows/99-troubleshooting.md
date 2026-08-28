# Workflow: troubleshooting

Read the whole error before doing anything - every tool here says what broke
and what to do about it.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`llm provider`: ...** Only affects the cosmetic governance note - see
  `docs/how-it-works.md`. Nothing else in this agent calls a model.
- **An adapter shows FAIL, not WARN.** `universal`/`built` adapters fail loud
  when misconfigured (`WARN` is reserved for stubs). Read the `detail`
  column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` calls `load_settings(demo=True)`, which forces the mock
  LLM provider and mock adapters regardless of `config/hotel.yaml` - if it
  still fails, the bug is in the fixtures or the engine, not your config.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose.

## The report says "nothing to report"

`sustain_daily` is empty. Import `data/imports/sustain_daily.csv` (see
`docs/integrations.md`), or run `make demo` first to see it work on the
bundled fixtures.

## The report has a warning banner about "only N day(s) of meter history"

Fewer than `report.min_history_days` (default 30) rows are in
`sustain_daily` - not a bug, and the report still computes from whatever is
there. Load more days and re-run.

## Every finding says "not being surfaced" / "single-day spikes are not being surfaced"

The `esg-anomaly` rule is off: `config/agent.yaml: anomaly.rules.esg-anomaly:
false`. Set it back to `true` to turn the scan back on.

## An anomaly is flagged but not named to a zone or floor

`data/imports/sustain_zone_daily.csv` has no rows for that date (or does not
exist). This is optional and honest - the report says exactly this rather
than guessing which zone. Add per-zone sub-metering to name it; see
`docs/integrations.md`.

## `python3 tools/review.py send` says "blocked ... mode is shadow"

Expected in `mode: shadow`. The approval is kept (`sending -> approved`, not
`failed`) - nothing needs re-approving once you flip to live. See
`docs/safety.md`.

## An item is stuck at `sending`

A process died mid-send. Every job's next pass calls
`core.store.Store.reap_stuck_sending()`, which moves anything stuck for more
than 30 minutes to `failed`. Use `python3 tools/review.py retry <id>` once
the cause is fixed.

## The dashboard CSV export never shows up

Check `mode` - the export is a guarded write like any other, so `shadow`
blocks it (`make doctor` and `tools/run.py`'s log both say so). Once live,
it happens automatically every run with no separate approval step; check
`data/exports/esg_dashboard.csv` or `python3 tools/doctor.py`'s sheets
adapter line.

## The numbers look wrong

Every finding names the baseline, the period, or the tariff that produced
it - re-read `python3 tools/review.py show <id>` in full before assuming a
bug. If the arithmetic really is wrong, `tests/test_sustainability_*.py` is
where to add a regression case before changing the engine.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision, in order, with a run id.
`python3 tools/review.py show <id>` has the full event trail for one item.
If neither explains it, that is a real bug - describe exactly what you ran
and what you expected, and ask.
