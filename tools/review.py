#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit / reject / send.

    python3 tools/review.py list [--status pending_review] [--kind esg_report]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --body-file draft.txt [--subject "..."] [--note "..."]
    python3 tools/review.py reject <id> --reason "wrong tone"
    python3 tools/review.py retry <id>          # re-queue a failed send
    python3 tools/review.py send                # email the report, notify engineering for alerts
    python3 tools/review.py stale               # go-live step: clear the shadow-era queue

Handles both item kinds this agent produces: `esg_report` (the monthly
report, emailed) and `engineering_alert` (one per flagged anomaly, sent as a
staff message). `list`/`show` work for either kind; `edit` only makes sense
for `esg_report` (an alert's text is short and generated, not usually
rewritten - reject and let the next run re-flag it if it still matters).

Only this tool writes `approved` / `edited` / `rejected` (core/review.py).
Nothing here bypasses `mode: shadow` - see docs/safety.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import get_email, get_messaging  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.log import Run  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject, retry,  # noqa: E402
                         show, stale_backlog)
from core.store import Store, StoreError  # noqa: E402


def _print_item_line(item) -> None:
    payload = item.payload or {}
    label = payload.get("period_label") or f"{payload.get('metric', '')} {payload.get('date', '')}"
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {item.kind:<17} "
          f"{label.strip()[:40]}{marker}".rstrip())


def cmd_list(store, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind, limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} item(s) waiting:\n")
    for item in items:
        _print_item_line(item)
    if any(item.is_sample for item in items):
        print("\n[SAMPLE DATA] One or more items above came from the shipped sample "
              "fixtures, not your property - the system that fed them is still on the "
              "'mock' adapter. Connect the real one (docs/integrations.md) before "
              "approving them.")
    print("\nRun `python3 tools/review.py show <id>` for the full draft.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    payload = (detail.get("item") or {}).get("payload") or {}
    if payload.get("_sample"):
        print("[SAMPLE DATA] This item came from the shipped sample fixtures, not your "
              "property - the system that fed it is still on the 'mock' adapter. Connect "
              "the real one (docs/integrations.md) before approving it.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    body = Path(args.body_file).read_text(encoding="utf-8")
    new_draft = dict(item.draft or {})
    if item.kind == "esg_report":
        new_draft["body_md"] = body
        if args.subject:
            new_draft["subject"] = args.subject
    else:
        new_draft["text"] = body
    edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another send attempt")
    return 0


def cmd_send(store, settings, args) -> int:
    with Run("review-send", settings, store) as run:
        claimed = store.claim_for_send(limit=args.limit)
        if not claimed:
            print("Nothing approved or edited is waiting to send.")
            run.stats = {"sent": 0, "failed": 0}
            return 0
        email = get_email(settings)
        messaging = get_messaging(settings)
        sent, failed = 0, 0
        for item in claimed:
            try:
                if item.kind == "esg_report":
                    draft = item.draft or {}
                    to = draft.get("to") or []
                    if not to or not any(to):
                        store.mark_send_failed(item.id, "no recipient - set report.recipients or "
                                               "contacts.manager.email in config/agent.yaml")
                        print(f"failed {item.id}: no recipient configured")
                        failed += 1
                        continue
                    result = email.send(to, draft.get("subject", ""), draft.get("body_md", ""),
                                        item=item)
                elif item.kind == "engineering_alert":
                    text = (item.draft or {}).get("text", "")
                    result = messaging.notify_staff(text, item=item)
                else:
                    store.mark_send_failed(item.id, f"'{item.kind}' has no send path")
                    print(f"failed {item.id}: unknown kind '{item.kind}'")
                    failed += 1
                    continue
            except WriteBlocked as exc:
                # Not a failure: the mode blocked it. The approval stands for go-live -
                # see core/store.py TRANSITIONS ("sending" -> "approved").
                store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
                print(f"blocked {item.id} (approval kept): {exc}")
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
                store.mark_send_failed(item.id, str(exc))
                print(f"failed {item.id}: {exc}")
                failed += 1
                continue
            store.mark_sent(item.id, result.get("message_id"))
            print(f"sent {item.id} ({item.kind})")
            sent += 1
        run.stats = {"sent": sent, "failed": failed}
    print(f"\n{sent} sent, {failed} failed.")
    return 0 if failed == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one item")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the draft unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the draft, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--body-file", required=True)
    p_edit.add_argument("--subject", default=None)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the draft")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="email the report, notify engineering for alerts")
    p_send.add_argument("--limit", type=int, default=20)

    sub.add_parser("stale", help="go-live step: mark everything still un-sent as stale "
                                 "(the shadow-era queue was never sent and is out of date)")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    try:
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "stale":
            moved = stale_backlog(store)
            print(f"marked {len(moved)} item(s) stale. Nothing from before go-live will be sent.")
            return 0
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"cannot do that: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
