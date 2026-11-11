"""Lernen: Verblassen der Beispiele, gezeigt-ohne-Klick als Gegenbeispiel."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from app import learn

NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def _ago(days):
    return (NOW - timedelta(days=days)).isoformat()


def _write(tmp_path, history, fb):
    (tmp_path / "history.jsonl").write_text("".join(json.dumps(r) + "\n" for r in history))
    (tmp_path / "feedback.jsonl").write_text("".join(json.dumps(r) + "\n" for r in fb))


def _row(eid, days, shown=True):
    return {"entry_id": eid, "ts": _ago(days), "features": {"topic_a": 1.0}, "shown": shown}


def test_dataset_weights_fade(tmp_path):
    _write(tmp_path,
           [_row("fresh", 1), _row("old", 61), _row("ignored", 30), _row("unseen", 30, shown=False),
            _row("undecided", 2)],
           [{"entry_id": "fresh", "kind": "click", "ts": _ago(0)},
            {"entry_id": "old", "kind": "star", "ts": _ago(60)}])
    X, y, w, stats = learn.build_dataset(tmp_path, feedback_days=7, half_life_days=30, now=NOW)
    assert y == [1, 1, 0, 0]                      # "undecided" ist noch zu frisch und fehlt
    assert w == pytest.approx([1.0, 1.5 * 0.25, 0.5, learn.UNSHOWN_WEIGHT * 0.5])
    assert (stats["click"], stats["star"], stats["neg_shown"], stats["neg_unshown"]) == (1, 1, 1, 1)


def test_dataset_without_fading(tmp_path):
    _write(tmp_path, [_row("old", 61), _row("ignored", 30)],
           [{"entry_id": "old", "kind": "star", "ts": _ago(60)}])
    _, _, w, _ = learn.build_dataset(tmp_path, feedback_days=7, half_life_days=0, now=NOW)
    assert w == [1.5, 1.0]


def test_late_click_turns_negative_into_positive(tmp_path):
    _write(tmp_path, [_row("a", 20)], [])
    assert learn.build_dataset(tmp_path, 7, 30, NOW)[1] == [0]
    _write(tmp_path, [_row("a", 20)], [{"entry_id": "a", "kind": "click", "ts": _ago(0)}])
    assert learn.build_dataset(tmp_path, 7, 30, NOW)[1] == [1]


def test_describe_mentions_fading(tmp_path):
    _write(tmp_path, [_row("a", 20)], [])
    assert "nach 30 Tagen noch halb" in learn.describe(tmp_path, 7, {}, 30)
    assert "Verblassen: aus" in learn.describe(tmp_path, 7, {}, 0)
