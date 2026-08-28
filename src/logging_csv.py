"""Appends every evaluation to data/scan_log.csv. This is the record."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

HEADER = ["timestamp", "ticker", "strike", "expiry", "dte", "delta", "bid",
          "ask", "iv", "failed_gate", "reason", "ann_roc", "score"]


class ScanLog:
    def __init__(self, path: str | Path = "data/scan_log.csv"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists() or self.path.stat().st_size == 0:
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(HEADER)
        self._rows: list[dict] = []

    def add(self, **kw) -> None:
        row = {k: kw.get(k, "") for k in HEADER}
        row["timestamp"] = kw.get("timestamp") or datetime.utcnow().isoformat(
            timespec="seconds")
        self._rows.append(row)

    def flush(self) -> int:
        if not self._rows:
            return 0
        with self.path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=HEADER)
            for r in self._rows:
                w.writerow(r)
        n = len(self._rows)
        self._rows = []
        return n
