# Workflow: working the review queue

Objective: turn a queued report or engineering alert into a decision, and,
once approved, actually send or notify it.

Nothing leaves the building without this. `mode: shadow` blocks every write
except an item you have explicitly approved or edited - see `docs/safety.md`.

## Two kinds, one command to see them

```bash
python3 tools/review.py list                              # everything waiting
python3 tools/review.py list --kind esg_report             # just the report
python3 tools/review.py list --kind engineering_alert --status needs_human
python3 tools/review.py show <id>
```

**`esg_report`** - the monthly report, emailed once approved.

**`engineering_alert`** - one per flagged anomaly, sent as a staff message
(WhatsApp/Slack/webhook, whatever `systems.messaging.adapter` is set to)
once approved. Short and generated from the anomaly's own numbers - usually
you approve or reject it rather than editing the wording; if it should not
go to engineering at all (a known one-off, already fixed), reject it with a
reason.

## Deciding

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt [--subject "New subject"]
python3 tools/review.py reject <id> --reason "already investigated, false alarm"
```

`edit` only makes sense for `esg_report` (rewrites `body_md`); on an
`engineering_alert` it rewrites the alert text. Either way the before/after
is recorded as a `learnings` row, even though this agent has no coach layer
to learn from it automatically.

## Sending

```bash
python3 tools/review.py send
```

Claims everything `approved`/`edited` (both kinds together), calls the
email adapter for the report and the messaging adapter for alerts, and
records the result. In `mode: shadow` this only ever works for nothing -
shadow blocks every send regardless of approval; see `docs/safety.md` for
exactly why that is not a bug. A shadow-mode block is **not** a failure:
the item goes straight back to `approved` (never `failed`), so nothing
needs re-approving - the next `python3 tools/review.py send` once
`mode: live` is set just sends it.

## A failed send

```bash
python3 tools/review.py retry <id>
```

re-queues it after the cause is fixed - usually a missing recipient
(`report.recipients` / `contacts.manager.email`) or a messaging credential
(`make doctor` will say which). A shadow-mode block never reaches `failed`,
so `retry` is for a real error, not for waiting out shadow mode.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
- `python3 tools/review.py stale` is the go-live step that clears everything
  approved during shadow mode - it was recorded but never sent, and is
  probably out of date by then.
