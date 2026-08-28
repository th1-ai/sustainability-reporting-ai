# Workflow: shadow to live

Objective: decide, together with the hotel, whether Sustainability
Reporting AI is ready to actually email the ESG report and notify
engineering about a flagged anomaly - and make the change safely if so.

This is the hotel's decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `WARN` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, currency and room
      count, and `config/agent.yaml`'s `tariffs:` are your own prices, not
      the shipped example.
- [ ] At least one real `python3 tools/run.py --once --report` has gone
      through the review queue against real `sustain_daily.csv` data - not
      just `make demo`'s fixtures.
- [ ] A real mailbox is connected (`systems.email.adapter: imap` or
      `gmail`) and `make doctor` shows it healthy - going live on `mock`
      would only ever touch the fixtures.
- [ ] A real messaging channel is connected for engineering alerts
      (`systems.messaging.adapter: webhook` or `unipile`), or you have
      accepted that alerts will queue but `send` will fail without one.
- [ ] `report.recipients` (or `contacts.manager.email`) is a real address.
- [ ] `python3 tools/review.py stale` has been run, so nothing approved
      during shadow goes out the moment mode flips.

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_email` and
   `send_message` by default - it should. Going live means **approved
   items get sent**, not that anything starts happening automatically. The
   `esg_dashboard` CSV/Sheets export is the one exception: `sheets_write` is
   not on that list by default, so once live it appends automatically every
   run, with no separate approval step - it is a log, not a message to a
   person.
3. Run `make doctor` again to confirm.
4. Watch one send go through end to end:
   ```bash
   python3 tools/run.py --once --report
   python3 tools/review.py list --kind esg_report
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
5. Tell the hotel exactly what just changed: an approved report now
   actually leaves the mailbox the next time `python3 tools/review.py send`
   runs (by hand or on the schedule); an approved engineering alert now
   actually reaches the messaging channel. Nothing is automatic before that
   approval - and the anomaly detector itself never touches a building
   system, ever, by design.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound send and the dashboard export on the next pass,
mid-schedule, with no other change required.
