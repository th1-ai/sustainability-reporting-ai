# Workflow: the Monthly ESG Report

Objective: turn 90 days of meter reads into an audit-ready report draft and,
if a day genuinely crossed the baseline, an engineering alert - both queued
for a human, nothing sent yet.

## Trigger

Production: monthly, on meter-read close (`config/agent.yaml: schedule.esg_report`,
default `monthly`). Manually, any time:

```bash
python3 tools/run.py --once --report
```

`python3 tools/run.py --once` (no `--report`) runs it only if the schedule
says it is due - useful on a cron/launchd/systemd timer, see
`workflows/90-go-live.md` and `scheduler/`.

## What happens, step by step

1. **Import.** `data/imports/sustain_daily.csv` and (if present)
   `sustain_zone_daily.csv` are re-imported automatically - you never run a
   separate import command, just overwrite the file and re-run.
2. **Normalise.** Every figure is restated per occupied room-night, in three
   30-day blocks (last 30 days, the 30 before that, the 30 before that) -
   the only number a group ESG team can compare across periods or
   properties. See `docs/how-it-works.md`.
3. **Scan for anomalies.** Runs once for electricity, once for water: a day
   is flagged when its per-room reading crosses `mean + escalate_sigma *
   stdev` across the 90-day window (`config/agent.yaml: anomaly:`). A flat
   series flags nothing - that is correct, not a bug.
4. **Price everything.** Every recommendation carries a euro-per-year figure
   at your own tariffs (`config/agent.yaml: tariffs:`).
5. **Queue the report.** One `esg_report` item, `pending_review`.
6. **Queue an alert per flagged day.** One `engineering_alert` item per
   anomaly, `needs_human` - see `workflows/80-review.md` for what happens to
   it.
7. **Export.** One row is appended to the `esg_dashboard` CSV/Sheets export
   - blocked in shadow mode exactly like a send would be; see
   `docs/how-it-works.md`.
8. **Governance note.** A cosmetic 2-3 sentence note from the LLM is
   appended to the report. Never changes a figure or a finding; skipped
   (not paused) when `llm.provider` is `interactive`.

## Checking the result

```bash
python3 tools/review.py list --kind esg_report
python3 tools/review.py show <id>
```

Read the report the way you would read it before sending it anywhere: does
the period look right, does the Method section describe what you actually
did, does a recommendation's price look plausible against your own tariffs?
`workflows/80-review.md` covers approving, editing or rejecting it.

## A month with too little history

Fewer than `report.min_history_days` (default 30) days in `sustain_daily`
produces a report with a warning banner instead of a failure - the KPI
tiles and findings still compute from whatever is there. Load more days and
re-run once you have a full month.

## Re-running the same month

Safe, any number of times. `esg_report` is keyed to the period label and
`engineering_alert` to `(metric, date)` of the flagged day - a re-run never
drafts the same report twice or duplicates an alert.
