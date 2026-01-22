from datetime import date, timedelta
from zoneinfo import ZoneInfo

def week_window_monday(today_local: date):
    # Monday anchor
    anchor = today_local - timedelta(days=today_local.weekday())  # Mon=0
    period_end = anchor + timedelta(days=6)  # Sunday
    return anchor, period_end

def remaining_days_inclusive(start: date, end: date) -> int:
    # inclusive days
    if end < start:
        return 0
    return (end - start).days + 1
