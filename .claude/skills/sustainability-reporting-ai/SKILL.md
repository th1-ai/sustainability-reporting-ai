---
name: sustainability-reporting-ai
description: Run Sustainability Reporting AI ("The Ranger") — Tracks energy, water, waste, and laundry volumes per occupied room, benchmarks month over month, drafts the ESG report sections, and flags anomalies (a spike in water use on floor 3) to engineering.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Ranger", "/sustainability-reporting-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Sustainability Reporting AI

Runs the Monthly ESG Report and works its review queue. Everything happens
from the repo root; every command below exists and works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-esg-report.md`
for the main loop. If the user has never run this agent, start at
`workflows/00-setup.md` instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines
are worth mentioning but do not stop the run - a fresh clone always warns
on the meter CSVs until real data is connected.

**2. Run the report.**

```bash
make run                              # only if the monthly job is due
make run ARGS="--report"              # force it now
make run ARGS="--report --dry-run"    # compute everything, write nothing
```

This agent's only LLM call (a cosmetic governance note) is **skipped, not
paused**, when `llm.provider` is `interactive` - unlike most agents in this
family, a normal run here never stops with exit code 3 waiting on a prompt.
See `docs/how-it-works.md` if the user asks why.

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Two kinds: `esg_report` (the monthly report) and `engineering_alert` (one
per genuine single-day anomaly - a flat month produces none). Summarise
each for the user in plain language: what period, what moved, what got
flagged and why. Do not paste raw JSON at them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path>
python3 tools/review.py reject <id> --reason "<why>"
```

Read the draft back to them before approving. If they want changes, write
the new version to a file and use `edit` - the before/after is stored.

**5. Report.**

```bash
make report
```

Shows run history, the human edit rate, engineering-alert outcomes, the
utility-cost-per-room trend against the roster's `-9%` target, and the
recommendations logged - with the honest caveat that "proposed" is not
"proven," see `docs/benefits.md`.

## Rules

- **Never send in shadow mode**, and never work around a blocked write. The
  error message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before sending the report or notifying engineering** - even
  when it is approved.
- **Never print or paste a credential.**
- **Never present the emissions estimate as a meter reading.** It is
  labeled "Estimated" for a reason - keep that label when you summarise it.
- If a run fails, read the whole error, fix the cause, re-run, and note
  what you learned in `workflows/99-troubleshooting.md`.
