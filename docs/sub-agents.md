# Sub-agents in this repo

None. Sustainability Reporting AI is top-level, with no children folded in
- `specs/briefs/sustainability-reporting-ai.md` lists none, and
`config/agent.yaml` has no `subagents:` block.

## Why per-zone data is a data source, not a sub-agent

The roster's own example - "a spike in water use on floor 3" - needs
per-zone or per-floor sub-metering, which sounds like it could be a
"Zone Monitoring" sub-agent. It is not one, on purpose: naming an anomaly to
a zone is a lookup against `sustain_zone_daily`, not a second decision
process with its own thresholds, its own queue, or its own review step. It
lives inside `esg_report.scan_anomalies()`'s single anomaly-detection pass -
see `docs/how-it-works.md` "Design decisions" #2-3. Feeding richer zone data
in makes the *existing* finding more specific; it does not add a new kind of
finding.

## The adjacent agent worth knowing about

**Portfolio Analyst AI** is the cross-property agent in this family. If you
run more than one property, that is where "compare Hotel Aurora's utility
cost per room against the rest of the portfolio" belongs - not here. This
repo's `esg_dashboard` export (`docs/integrations.md`) is shaped so its
rows can feed that agent, or a plain spreadsheet, without this repo needing
to know anything about other properties.

## If you want to fold in a real sub-agent later

Follow the shape every other repo in this family uses: `tools/<name>.py`
(its own engine), `workflows/2x-<name>.md`, a `subagents.<name>.enabled`
block in `config/agent.yaml` (off by default), its own fixtures, and a
README block under "Sub-agents in this repo." Ask your Claude session to
build it the same way `docs/integrations.md#implement-your-own` describes
adding an adapter.
