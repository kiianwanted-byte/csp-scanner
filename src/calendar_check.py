"""US market calendar. Exit silently on holidays and weekends."""
from __future__ import annotations

from datetime import date


def is_trading_day(d: date | None = None) -> bool:
    d = d or date.today()
    if d.weekday() >= 5:
        return False
    try:
        import pandas_market_calendars as mcal
        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=d, end_date=d)
        return not sched.empty
    except Exception:  # noqa: BLE001
        # Library missing or failing. Weekday check already applied.
        return True
