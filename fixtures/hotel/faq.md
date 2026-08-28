# Hotel Aurora - fixture notes for the bundled meter data

Not read by any prompt (this agent has no guest-facing text and no FAQ
lookup) - this is a plain-English key to the bundled 90-day fixture so
anyone reading the repo can see the story the numbers tell.

- **Electricity** falls steadily across the 90 days: about 10.6 kWh per
  occupied room in the oldest 30-day block, 10.1 in the middle block, 9.3 in
  the most recent - a new HVAC/lighting schedule that is holding.
- **Water** is flat except for one day (2026-08-15), when it more than
  doubled. Floor 3's sub-meter shows it took the large majority of that
  day's total - a leak or a valve left open is the likely cause, not a
  wider drift.
- **Waste** sits a little above the 4.0 kg/occupied-room group benchmark
  used in `config/agent.example.yaml`.
- **Laundry** is falling gently, in line with occupancy.

This is what makes `make demo` produce: an improving electricity story, a
genuine single-day water anomaly named to Floor 3 (the roster's own
example - "a spike in water use on floor 3"), and no electricity anomaly at
all (a flat electricity series stays flat - see
`docs/how-it-works.md` "Design decisions" #3).
