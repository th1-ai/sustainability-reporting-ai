# Guardrails and safety

This agent reads your meter data and reports on it. It never touches a
building system and never talks to a guest. Everything below is built in,
not optional, and this page explains what it does and what is left for you
to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, computes and drafts the report and any anomaly alerts, and queues them. It **never** emails the report, **never** notifies engineering, and **never** writes to the consumption dashboard export. Approving, editing or rejecting a draft records your decision but sends nothing. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | Items you approved are really sent: the report by email, an alert as a staff message. The dashboard export happens automatically every run (it is a log, not a message to a person - see below). Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it
back to `shadow` stops every outbound action immediately, mid-schedule, with
no other change. `config/agent.yaml` can be stricter than `hotel.yaml`,
never looser.

Two more brakes:

- `python3 tools/run.py --once --dry-run` computes everything and writes
  nothing, even in live mode. Use it when you change a tariff or a
  threshold.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions
  that need a human even in live mode. The defaults are `send_email` and
  `send_message` - both the report and every engineering alert need a
  person's approval before they leave, in shadow or in live. `sheets_write`
  (the dashboard export) is deliberately **not** on that list - see "The one
  write that isn't gated by approval" below.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches an inbox or a staff channel without passing through the
queue.

```bash
make review                        # what is waiting
python3 tools/review.py show <id>   # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "already investigated"
```

An `esg_report` moves `new -> pending_review` and waits for a person. An
`engineering_alert` moves `new -> needs_human` and waits for a person - only
a genuine anomaly (baseline + several standard deviations, see
`docs/how-it-works.md`) creates one; a flat month creates none. Only
`tools/review.py` can write `approved`, `edited` or `rejected`; only
`tools/run.py` can write `sending`/`sent`. A crash between "about to send"
and "sent" is picked up on the next pass and shown to you as failed rather
than silently retried.

## The one write that isn't gated by approval

`sheets_write` (the row appended to the `esg_dashboard` CSV/Sheets export)
is not on `review.require_approval_for` by default. In `live` mode it
happens automatically, every run, with no separate approval step - it is a
log of what was already in the report, not a new message to a person or a
system. It is still fully blocked in `shadow` mode, exactly like a send -
`mode` is the kill switch for every write in this repo, gated or not.

## What the agent will not do

- **Control anything.** There is no write path to a BMS, a meter, or any
  building system anywhere in this codebase. The roster is explicit -
  "reports and flags; it doesn't control building systems" - and that is
  structurally true, not just a policy.
- **Send anything while `mode: shadow`.**
- **Send an item a human has not approved.**
- **Invent a meter reading, a tariff, or an emissions factor.** Every
  consumption and cost figure traces to a row in `sustain_daily`, at the
  tariffs in your own `config/agent.yaml` - see the Method section printed
  in every report.
- **Present the emissions estimate as a measurement.** The CO2e figure uses
  a configurable grid factor, not a meter reading, and is printed under its
  own "Estimated emissions" heading, structurally separate from the metered
  table - see `docs/how-it-works.md` "Design decisions" #7.
- **Claim a zone it cannot see.** An anomaly is only named to a floor or
  zone when `sustain_zone_daily.csv` actually has rows for that date. With
  no per-zone data connected, the finding says so in plain words instead of
  guessing.
- **Show a "saving" that is actually a regression.** Every recommendation's
  impact is floored at zero (`max(0, ...)`) - a month that got worse never
  renders as money saved.
- **Claim GRI / CSRD / GSTC / Green Key / EU Taxonomy compliance.** This is
  a well-argued utility review with a priced action list, not a mapped
  disclosure filing - see `docs/how-it-works.md` "Design decisions" #8.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or
`claude-code`, the governance-note prompt goes to Anthropic. That prompt
contains only the report's own summary numbers (period, per-room figures,
finding titles, anomaly count) - never a raw meter reading table, never a
guest's or a staff member's name. With `llm.provider: mock` or
`interactive`, nothing leaves the machine at all.

**No guest data passes through this agent at all.** `sustain_daily` and
`sustain_zone_daily` are meter readings and occupancy counts - there is no
guest name, email, phone number or reservation reference anywhere in this
agent's data model. `core/redact.py`'s card-number redaction exists in
every repo in this family but has nothing to redact here.

**What is stored, and where.** Everything lives in `data/` inside this
folder: `agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is
gitignored. There is no cloud service behind this repo and no telemetry.

**Retention.** `privacy.retention_days` (default 365) is how long processed
items stay in the database. Deleting `data/agent.db` deletes everything the
agent knows - meter history included, so keep your own export of
`sustain_daily.csv` if you want a longer record than that.

## GDPR and AI-transparency, in practice

**There is nothing to disclose to a guest.** This agent produces an internal
report for ownership and an internal alert for engineering - it never
drafts or sends anything a guest reads, so the EU AI Act Article 50
guest-disclosure line that the rest of this family carries does not apply
here. If your own report distribution includes recipients outside the
property (a group ESG team, an auditor), tell them plainly that the report
is AI-drafted and human-reviewed before it reaches them, the same way you
would credit any automated tool in a report you sign off on.

**You are still the controller of what little personal data touches this
repo** - contact emails in `config/agent.yaml: report.recipients` and
`config/hotel.yaml: contacts`, nothing else. If you use the `anthropic` or
`claude-code` provider, Anthropic processes the report's summary numbers as
a processor; check their data processing terms if that matters to your
own compliance register.

## Subscription or API: an honest note

Two ways to pay for the one reasoning step this agent makes (the cosmetic
governance note - everything else is arithmetic):

**Your Claude Code subscription** (`llm.provider: claude-code` or
`interactive`). Flat monthly cost, and this agent makes at most one LLM
call a month. This is comfortably the cheapest way to run it, and
`interactive` skips the call entirely rather than pausing your report on a
writing prompt - see `docs/how-it-works.md`.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to
automated use of it. One call a month is a trivial load either way.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token. For an
agent that calls a model once a month, this is not where the cost of
running it comes from either way - pick whichever is more convenient.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`.
   Every outbound action, including the dashboard export, stops on the next
   pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now sustainability-reporting-ai.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
