"""The [SAMPLE DATA] marker in tools/review.py.

On a fresh clone every adapter is still `mock`, so anything the queue reads
through email or messaging (both declared in `systems_used`) is the shipped
sample data, not the hotel's own property. Neither `review.py list` nor
`review.py show` may let that pass unmarked.
"""

from __future__ import annotations

from argparse import Namespace

from review import cmd_list, cmd_show


def _sample_item(isolated_settings, store_at, source: str = "email"):
    settings = isolated_settings(provider="mock", mode="shadow")
    store = store_at(settings)
    item = store.upsert_item(source, f"{source}-esg-2026-07", kind="esg_report",
                             payload={"period_label": "July 2026", "kwh_per_room": 11.4,
                                      "cost_per_room": 2.05, "anomalies": 1})
    store.transition(item.id, "pending_review", "agent")
    return store, store.get_item(item.id)


def test_mock_sourced_item_is_tagged_as_sample(isolated_settings, store_at):
    _store, item = _sample_item(isolated_settings, store_at)
    assert item.is_sample is True


def test_list_marks_the_sample_item(isolated_settings, store_at, capsys):
    store, item = _sample_item(isolated_settings, store_at)
    cmd_list(store, Namespace(status=None, kind=None, limit=50))
    out = capsys.readouterr().out
    assert item.id in out
    assert "[SAMPLE DATA]" in out


def test_show_marks_the_sample_item(isolated_settings, store_at, capsys):
    store, item = _sample_item(isolated_settings, store_at, source="messaging")
    cmd_show(store, Namespace(id=item.id))
    out = capsys.readouterr().out
    assert out.startswith("[SAMPLE DATA]")
