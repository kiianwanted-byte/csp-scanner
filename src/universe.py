"""Reads the approved ticker universe. Approved only, no exceptions."""
from __future__ import annotations

import csv
from pathlib import Path


class UniverseError(Exception):
    pass


def load_universe(path: str | Path = "data/tickers.csv") -> list[dict]:
    p = Path(path)
    if not p.exists():
        raise UniverseError(f"ticker file not found: {p}")

    approved = []
    with p.open(newline="") as f:
        reader = csv.DictReader(f)
        required = {"ticker", "sector", "approved"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise UniverseError(f"{p} must have columns: {sorted(required)}")
        for row in reader:
            if str(row.get("approved", "")).strip().upper() == "TRUE":
                approved.append({
                    "ticker": row["ticker"].strip().upper(),
                    "sector": row["sector"].strip(),
                    "notes": row.get("notes", "").strip(),
                })
    return approved


def load_open_positions(path: str | Path = "data/open_positions.csv") -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if not r.get("ticker"):
            continue
        try:
            strike = float(r.get("strike", 0) or 0)
            prem = float(r.get("premium_received", 0) or 0)
            contracts = int(float(r.get("contracts", 1) or 1))
        except ValueError:
            continue
        out.append({
            "ticker": r["ticker"].strip().upper(),
            "sector": r.get("sector", "").strip(),
            "collateral": (strike - prem) * 100 * contracts,
        })
    return out
