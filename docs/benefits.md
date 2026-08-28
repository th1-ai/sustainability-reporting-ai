# Measuring the benefit

The roster's own numbers for this agent:

- **Output.** "Per-property consumption dashboards and audit-ready ESG
  report drafts."
- **ROI.** `-9%` on "Utility cost per occupied room" (labor-type saving -
  time not spent building the report by hand, plus the utility spend the
  recommendations target).

## What to track

```bash
make report
```

Shows, from real data, no recomputation:

- **How many reports have run**, and the last few periods' headline
  numbers (electricity per room, anomaly count, whether the dashboard
  export actually went out).
- **Human edit rate on the report** - how often the draft needed a rewrite
  before it was fit to send. A falling edit rate over the first few months
  is the honest sign that the report is getting closer to right without you
  correcting it every time (this agent has no coach layer to *automate*
  that improvement - the correction has to come from you editing
  `tools/esg_report.py`'s wording or `config/agent.yaml`'s thresholds by
  hand).
- **Engineering alerts by status** - how many anomalies fired, how many
  were approved and sent, how many were rejected as false alarms. A
  consistently high false-alarm rate is a sign `anomaly.flag_sigma` /
  `escalate_sigma` in `config/agent.yaml` are set too tight for this
  property's normal variance; a spike that never gets flagged is the
  opposite problem.
- **Utility cost per occupied room, by report period** - the roster's own
  metric, plotted period over period. `make report` also prints the
  percent change since the prior report next to the `-9%` target.
- **Recommendations logged, and their cumulative proposed annual saving.**

## What this does not prove

`make report`'s "proposed vs. what actually happened" line is exactly that
- a comparison, not a causal claim. Nothing in this repo ties a specific
recommendation to a specific later change in `sustain_daily`. A hotel
that wants a rigorous version of "-9%" needs a **banked savings ledger**:
mark a recommendation as "actioned" on the date it was actually done (BMS
schedule changed, aerators fitted, laundry policy switched), then compare
only the cost-per-room trend *after* that date against the trend before it.
`sustain_recommendations` already has the `period_label`, `title` and
`impact` columns a ledger like that would need - ask your Claude session to
add an `actioned_at` column and the comparison query if you want the
honest version of this number rather than the proposed one.

## Other honest caveats

- **No CO2e accounting rigor.** The "Estimated emissions" section is a
  Scope 2, location-based estimate from one configurable grid factor - not
  a Scope 1/3 inventory, not third-party verified, and not a substitute for
  whatever your actual reporting framework requires. See
  `docs/how-it-works.md` "Design decisions" #7-8.
- **One property per report.** "Per-property consumption dashboards" is
  plural in the roster; this repo runs one property per clone. If you run
  more than one, **Portfolio Analyst AI** is the cross-property agent - the
  `esg_dashboard` export (`docs/integrations.md`) is deliberately shaped so
  rows from several properties can be concatenated into it by hand or by
  that agent.
- **Data quality is the ceiling.** The roster says it plainly - "data
  quality depends on the meters and invoices it's given." A property with
  patchy or estimated meter reads will get a report that is honest about
  the gaps (see the warning banner for a short history) but cannot be more
  accurate than what it was fed.
