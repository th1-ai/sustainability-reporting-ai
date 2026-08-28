# Workflow: first-run setup

Objective: get Sustainability Reporting AI from a fresh clone to a working
demo, then to real meter data, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet). `make doctor` will
   show a `FAIL` on "hotel identity" right after setup - expected, the
   property name is still the shipped placeholder. It will also `WARN` on the
   two `data/imports/*.csv` checks - also expected, there is no real meter
   export yet.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   This seeds the bundled fixtures (an invented hotel, "Hotel Aurora", 90
   days of meter reads) and runs the Monthly ESG Report. Expect a
   seeded-fixtures summary, "Monthly ESG Report: drafted, queued for review
   (1 engineering alert(s))", a review-queue summary, and the line
   `DEMO OK`. The one engineering alert is a genuine single-day water spike
   the fixtures name to "Floor 3" - the roster's own example, working. If
   you do not see `DEMO OK`, stop and read `workflows/99-troubleshooting.md`
   before going further.

   `make demo` writes to its own `data/demo/demo.db`, never `data/agent.db`
   (that is `make run`'s file), and only ever reads the bundled fixtures -
   never a real `data/imports/*.csv` even if you connect one later. Run
   `make demo` as many times as you like at any point; it never affects a
   real pass and a real pass never affects it. `make clean` deletes runtime
   state - `data/agent.db`, `data/demo/`, `data/logs/`, `data/exports/`,
   `data/pending/` - if you ever want a blank slate. It does **not** touch
   `data/imports/` (your imported meter CSVs), `config/`, or `.env` - your
   real meter data survives a `make clean`. To drop the imported CSVs too,
   run `rm -rf data/imports` yourself.

3. **Fill in the property.** Edit `config/hotel.yaml` (name, address,
   contact, currency, timezone, room count). Then:
   ```bash
   cp knowledge/property.example.md   knowledge/property.md
   cp knowledge/faq.example.md        knowledge/faq.md
   ```
   Neither file is read by this agent's own logic (there is no guest-facing
   text here) - they exist for anyone, including your Claude session,
   working on this repo later. See `knowledge/README.md`.

4. **Put your own tariffs in.** In `config/agent.yaml`'s `tariffs:` block,
   replace the example electricity/water/waste/laundry prices with what your
   utility invoices actually charge, and `grid_kgco2_per_kwh` with your own
   grid operator's published emissions factor if you know it (the shipped
   default is a rough EU-wide average). Every euro figure in the report
   traces back to these - see `docs/how-it-works.md` "Design decisions" #5.

5. **Set who reads the report, and who hears about a spike.**
   - `report.recipients` in `config/agent.yaml`: who gets the monthly ESG
     report. Falls back to `contacts.manager.email` in `config/hotel.yaml`
     if left empty.
   - `systems.messaging.adapter` in `config/hotel.yaml`: how an approved
     engineering alert reaches engineering - `webhook` (Zapier/Make/n8n, no
     setup) is the easiest to start with. See `docs/integrations.md`.

6. **Pick how the agent thinks.** `config/agent.yaml`'s `llm.provider`
   starts as `interactive`. The only reasoning step in this whole agent is a
   cosmetic 2-3 sentence governance note appended to the report - everything
   else is arithmetic. On `interactive` that note is skipped entirely rather
   than pausing the run, so you will not see a pending prompt from this
   agent day to day. See `docs/how-it-works.md` for the other three
   providers and when the note is worth turning on.

7. **Connect your real meter data (optional for now).**
   `docs/integrations.md` covers the exact CSV columns for
   `data/imports/sustain_daily.csv` (required) and
   `data/imports/sustain_zone_daily.csv` (optional per-zone/floor
   sub-metering - without it, an anomaly is still flagged, just not named to
   a zone). Run `make doctor` after adding either.

8. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real and at least `sustain_daily.csv` is
   connected, move on to `workflows/10-esg-report.md` to run the loop for
   real.
