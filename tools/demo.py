#!/usr/bin/env python3
"""tools/demo.py - one full cycle on the bundled fixtures. No credentials needed.

    make demo
    python3 tools/demo.py

Seeds `fixtures/inbound/*.json` into its OWN database, `data/demo/demo.db` -
never `data/agent.db`, which is `make run`'s file and may hold a hotel's real
reports and decisions. Every `make demo` deletes and rebuilds `data/demo/demo.db`
from scratch, so it always shows the same result and can never accumulate a
prior run's drafts.

Runs the Monthly ESG Report with `load_settings(demo=True)` (mock LLM
provider, shadow mode, mock adapters, whatever config/hotel.yaml says) and
`run_esg_report(..., source="demo")`, which skips the `data/imports/*.csv`
import step entirely - the demo reports on the bundled fixtures ONLY, even
when a hotel's own real CSVs already sit in `data/imports/` (Finding 3 and
Finding 4: neither direction of contamination is allowed - demo data never
reaches `data/agent.db`, and a hotel's real data never reaches the demo).
Nothing leaves this machine either way - the dashboard export is blocked in
shadow mode exactly like an email send would be.

`make clean` removes both `data/agent.db` and `data/demo/` - see README §6/§9
and workflows/00-setup.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings, sub_data_dir  # noqa: E402
from core.log import Run, summary_line  # noqa: E402
from core.review import queue_summary  # noqa: E402
from core.store import Store  # noqa: E402

import store_ext  # noqa: E402
from run import run_esg_report  # noqa: E402


def main() -> int:
    settings = load_settings(demo=True)
    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run
    store = Store(settings, path=demo_db)
    try:
        store_ext.migrate(store)
        seeded = store_ext.seed_fixtures(store, REPO_ROOT / "fixtures" / "inbound")
        print("Seeded fixtures:")
        for table, n in seeded.items():
            print(f"  {table}: {n} row(s)")
        print()

        stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0}
        with Run("demo", settings, store) as run:
            queued = run_esg_report(settings, store, provider=None, dry_run=False, source="demo")
            stats["processed"] += 1
            stats["drafted"] += 1 if queued else 0
            stats["needs_human"] += max(0, queued - 1) if queued else 0
            print(f"Monthly ESG Report: {'drafted' if queued else 'nothing to report'}, "
                 f"queued for review ({max(0, queued - 1)} engineering alert(s)).\n")
            run.stats = dict(stats)

        summary = queue_summary(store)
        print("Review queue:")
        for status, count in sorted(summary["by_status"].items()):
            print(f"  {status}: {count}")
        print(f"\n{summary['waiting_on_human']} item(s) waiting on a human. "
             f"Run `make review` to see them.\n")

        print(summary_line(stats, settings.mode))
        print("DEMO OK")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
