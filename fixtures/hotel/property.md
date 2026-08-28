# Hotel Aurora - property facts (fixture)

Used by `make demo` and the tests. Not read by any prompt in this agent (the
governance note draws its property facts straight from `config/hotel.yaml`
via `core.templates.hotel_block()`) - this file documents the invented
property behind the bundled meter fixtures, for anyone reading the repo.

- Name: Hotel Aurora
- Rooms: 42 (occupied room-nights in the fixtures run 26-33 most days)
- Location: Lisbon, Portugal
- Currency: EUR
- Utilities: metered electricity, water and waste per day, whole property.
  Floor-level electricity and water sub-metering exists on floors 1-3 only
  (`fixtures/inbound/sustain_zone_daily.json`) - the rest of the property
  reports at the property level only.
- Laundry: an in-house laundry, kg processed per day, whole property.
- Meter-read close: the 1st of each month, for the month just ended.
