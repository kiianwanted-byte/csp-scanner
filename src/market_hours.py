"""Is the US market open right now? Removes the DST cron problem."""
from __future__ import annotations

from datetime import datetime, timezone


def market_status(now: datetime | None = None) -> tuple[bool, str]:
    """Returns (is_open_and_settled, reason).

    'Settled' means at least 30 minutes past the open, so quotes are real.
    Handles DST automatically via the exchange calendar.
    """
    now = now or datetime.now(timezone.utc)
    d = now.date()

    if d.weekday() >= 5:
        return False, f"{d} is a weekend"

    try:
        import pandas_market_calendars as mcal
        import pandas as pd

        nyse = mcal.get_calendar("NYSE")
        sched = nyse.schedule(start_date=d, end_date=d)
        if sched.empty:
            return False, f"{d} is a US market holiday"

        open_utc = sched.iloc[0]["market_open"].to_pydatetime()
        close_utc = sched.iloc[0]["market_close"].to_pydatetime()
        settled = open_utc + pd.Timedelta(minutes=30)

        if now < settled:
            return False, (f"too early (opens {open_utc:%H:%M} UTC, "
                           f"settles {settled:%H:%M} UTC, now {now:%H:%M})")
        # Only run inside a 45 min window after settling. The workflow fires
        # at two UTC times to cover DST; this makes exactly one of them win.
        window_end = settled + pd.Timedelta(minutes=45)
        if now > window_end:
            return False, (f"outside scan window "
                           f"({settled:%H:%M}-{window_end:%H:%M} UTC, "
                           f"now {now:%H:%M})")
        if now > close_utc:
            return False, f"market closed at {close_utc:%H:%M} UTC"
        return True, f"open, {now:%H:%M} UTC"
    except ImportError:
        return True, "calendar library unavailable, proceeding"
    except Exception as e:  # noqa: BLE001
        return True, f"calendar check failed ({e}), proceeding"
