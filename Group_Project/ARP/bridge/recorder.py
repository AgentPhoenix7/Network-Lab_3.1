"""Run recorder: in-memory event list plus runs/<run>.jsonl. Adds seq (gap-free from 0), t and run (contract §3.1)."""
import json
from pathlib import Path


class Recorder:
    def __init__(self, run_id: str, runs_dir) -> None:
        self.run_id = run_id
        self.path = Path(runs_dir) / f"{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("")  # a new run always starts with an empty file
        self.events: list[dict] = []

    def add(self, ev: dict, t_ms: int) -> dict:
        """Stamp ev with seq, t (sim-ms since run start) and run, store it, append it to the file."""
        event = {**ev, "seq": len(self.events), "t": t_ms, "run": self.run_id}
        self.events.append(event)
        with self.path.open("a") as f:
            f.write(json.dumps(event) + "\n")
        return event
