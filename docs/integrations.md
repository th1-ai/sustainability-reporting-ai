# Connecting your systems

Every connector here is one of three things, and the table says which.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: CSV, IMAP/SMTP, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working right now:

```bash
make doctor
```

## What this agent actually reads and writes

The one job does **not** read a live BMS, meter, or PMS API directly. Meter
reads and occupancy are a monthly bulk export - that is how a facilities or
finance team actually gets this data - so they arrive as CSV files in
`data/imports/` and are loaded into `data/agent.db` by `tools/store_ext.py`,
not through `core/adapters`. The `core/adapters` registry is used for
exactly two things: **emailing** the ESG report, and **notifying**
engineering about a flagged anomaly.

### The CSV meter exports (universal - always works, start here)

<a id="sustain-daily"></a>
<a id="sustain-zone-daily"></a>

| File | Feeds | Columns | Required? |
|---|---|---|---|
| `data/imports/sustain_daily.csv` | Monthly ESG Report | `date, kwh, water_m3, waste_kg, laundry_kg, occupied_rooms` | Yes |
| `data/imports/sustain_zone_daily.csv` | Naming a flagged anomaly to a zone/floor | `date, zone, kwh, water_m3` | No - see below |

Headers are matched case-insensitively; `water_m3`/`water` and
`laundry_kg`/`laundry` both work. `sustain_daily.csv` **accumulates** - each
import adds or updates rows by date, so a monthly export just keeps
extending the meter table. Sample files with the exact shape:
`fixtures/inbound/sustain_daily.sample.csv` and
`fixtures/inbound/sustain_zone_daily.sample.csv`.

**You do not run a separate import command.** `tools/run.py` re-imports both
files automatically, right before the report reads the table. Save your
export over the old file in `data/imports/` and the next
`python3 tools/run.py --once --report` picks it up - `make doctor` also runs
these same loaders (against a throwaway copy, never your real database) so
it can tell you the exact row count it found, not just that the file exists.

**`occupied_rooms` is the denominator for everything.** It is usually the
same occupancy export your PMS already produces for other reporting -
`docs/how-it-works.md` "Design decisions" #9 explains why this agent reads
it as a plain column rather than calling a live PMS API.

**`sustain_zone_daily.csv` is genuinely optional.** Without it, an anomaly
is still detected and still flagged to engineering - it just says plainly
that no per-zone sub-metering is connected, at the property level, instead
of guessing which zone. Add it once your BMS or sub-meters can export
per-floor or per-zone electricity/water reads. This is what makes the
roster's own example possible: "a spike in water use on floor 3."

### Email - `systems.email.adapter`

<a id="email"></a>

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Writes to `data/exports/sent_email.jsonl`. What `make demo` uses. |
| `imap` | universal | mailbox + app password | Any provider. **Start here for real sends.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

This agent only ever calls `send()` for the report - it never reads a
mailbox.

**`imap` (start here).** In `.env`:

```
EMAIL_ADDRESS=sustainability@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
```

**`gmail` setup (about ten minutes, once).** In
[Google Cloud Console](https://console.cloud.google.com/), enable the Gmail
API, configure the OAuth consent screen, create a **Desktop app** OAuth
client, and save the downloaded JSON as `credentials.json` in this repo's
root (gitignored - never commit it). Then:

```bash
.venv/bin/pip install google-api-python-client google-auth-oauthlib
```

Set `systems.email.adapter: gmail` and run `make doctor`; the first run
opens a browser once to sign in and writes `token.json`, then refreshes
silently.

The signature (`knowledge/signature.md`) is appended to every send
automatically - see `Email.with_signature()` in `core/adapters/base.py`.

### Messaging - `systems.messaging.adapter` (engineering alerts)

<a id="messaging"></a>

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | What `make demo` uses. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. **Easiest to start with.** |

This agent only ever calls `notify_staff()`, once approved, for an
engineering alert. **`webhook`** is the fastest path: set
`MESSAGING_WEBHOOK_URL` and the agent POSTs `{chat_id, text, kind, hotel,
sent_at}` - point it at a Slack/Teams/email-relay automation and you are
done in five minutes. **`unipile`** needs your own account and your own
connected WhatsApp/staff number; WhatsApp Business policy limits what you
may send outside a guest-initiated window (not relevant here - this is
staff-to-staff, not guest-facing - but read your provider's rules).

### Sheets - `systems.sheets.adapter` (the consumption dashboard)

<a id="sheets"></a>

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/esg_dashboard.csv`. What `make demo`/shadow mode uses (and shadow blocks it - see `docs/how-it-works.md`). |
| `google` | built | service account JSON | A live shared spreadsheet, useful once you run more than one property and want to compare by hand or with **Portfolio Analyst AI**. |

One row is appended per report period: hotel, period, the four per-room
figures, cost per room, kg CO2e per room, and the two headline deltas - see
`esg_report.DASHBOARD_HEADER` in `tools/esg_report.py`.

### PMS - `systems.pms.adapter` (configured, not used by the core loop)

<a id="pms"></a>

`systems.pms` is configured (mock by default) because `make doctor` checks
every adapter, but this agent's report never calls it - occupied room-nights
come from the `occupied_rooms` column in `sustain_daily.csv`, not a live PMS
read (see "Design decisions" #9 in `docs/how-it-works.md`). If you want a
cross-check between your PMS's own occupancy figures and what is in
`sustain_daily.csv`, ask your Claude session to add one; `core/adapters/pms_csv.py`
and `pms_cloudbeds.py` are both already built and ready to read from.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments`, `procurement` and
`locks` are **stubs**, unused by this agent.

## Implement your own

<a id="implement-your-own"></a>

Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I want the ESG report emailed through **<your mail provider>**. Copy the
> shape of `core/adapters/email_imap.py`, implement `ping`, `capabilities`
> and `send` first, register it in `core/adapters/__init__.py`'s `email`
> family, and stop so I can check `make doctor` before anything else.

### The five steps

**1. Copy the closest existing adapter.** `core/adapters/email_imap.py` for
a mailbox, `messaging_webhook.py` for a chat channel, `sheets_google.py` for
a live spreadsheet.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

**3. Implement the reads**, if any.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("send_message")
def notify_staff(self, text: str) -> dict:
    ...
```

Not optional - without it the adapter can write while the agent is in
shadow mode, which defeats the whole safety model.

**5. Register it.** One line in `core/adapters/__init__.py`'s registry
table, then set the matching `systems.<x>.adapter` in `config/hotel.yaml`
and run `make doctor`.

### Rules that matter

- **`ping()` never raises.**
- **Every write is decorated with `@guarded_write`.** No exceptions.
- **Never log a credential.**
- **Write a test.** Copy one of `tests/test_sustainability_run.py`'s cases -
  no network needed for the engine tests, and a small adapter test can use
  `unittest.mock` for the HTTP layer.

### `core/` is shared

`core/` is identical in all 28 agents in this family. A hotel-specific
tweak belongs in `tools/` or your own adapter file, never in `core/`.
