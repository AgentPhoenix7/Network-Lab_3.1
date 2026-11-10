"""Recorder: seq is gap-free from 0, t and run are stamped, and the jsonl file mirrors memory."""
import json

from bridge.recorder import Recorder


def test_seq_is_gap_free_from_zero(tmp_path):
    rec = Recorder("run1", tmp_path)
    for i in range(1000):
        rec.add({"ev": "tx", "node": "h1"}, t_ms=i * 10)
    assert [e["seq"] for e in rec.events] == list(range(1000))


def test_event_gets_t_and_run(tmp_path):
    rec = Recorder("2026-11-10T09-30-00", tmp_path)
    event = rec.add({"ev": "arp", "op": "pending"}, t_ms=10)
    assert event == {"ev": "arp", "op": "pending", "seq": 0, "t": 10, "run": "2026-11-10T09-30-00"}


def test_jsonl_file_matches_memory(tmp_path):
    rec = Recorder("run2", tmp_path)
    rec.add({"ev": "ready", "node": "h1"}, t_ms=0)
    rec.add({"ev": "rx", "node": "h2", "verdict": "accepted"}, t_ms=300)
    lines = (tmp_path / "run2.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == rec.events


def test_new_recorder_truncates_existing_file(tmp_path):
    Recorder("run3", tmp_path).add({"ev": "log"}, t_ms=0)
    Recorder("run3", tmp_path)
    assert (tmp_path / "run3.jsonl").read_text() == ""
