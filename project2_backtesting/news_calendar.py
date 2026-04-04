"""
Historical news calendar — high-impact economic events.
Used to filter out trades that would have entered during news blackout periods.
"""

import pandas as pd
import os
import json

_HERE = os.path.dirname(os.path.abspath(__file__))
CALENDAR_PATH = os.path.join(_HERE, 'data', 'news_calendar.json')

# Hardcoded major recurring events (always same schedule)
# NFP: first Friday of each month, 13:30 UTC
# FOMC: ~8 times per year, 19:00 UTC (dates vary)
# CPI: ~12th of each month, 13:30 UTC

def get_nfp_dates(start_year=2020, end_year=2026):
    """Generate NFP dates (first Friday of each month)."""
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            first_day = pd.Timestamp(year=year, month=month, day=1)
            # Find first Friday
            days_until_friday = (4 - first_day.dayofweek) % 7
            nfp_date = first_day + pd.Timedelta(days=days_until_friday)
            # NFP is at 13:30 UTC
            nfp_time = nfp_date.replace(hour=13, minute=30)
            dates.append(nfp_time)
    return dates


def is_news_blackout(timestamp, blackout_minutes=5):
    """
    Check if a timestamp falls within a news blackout period.

    Currently checks:
    - NFP: first Friday of month at 13:30 UTC ± blackout_minutes
    - FOMC: 8 known dates per year at 19:00 UTC ± blackout_minutes
    - CPI: ~12th of month at 13:30 UTC ± blackout_minutes

    Returns True if trading should be skipped.
    """
    ts = pd.to_datetime(timestamp)
    blackout = pd.Timedelta(minutes=blackout_minutes)

    # NFP check: first Friday of month at 13:30
    first_day = ts.replace(day=1)
    days_until_friday = (4 - first_day.dayofweek) % 7
    nfp_date = first_day + pd.Timedelta(days=days_until_friday)
    nfp_time = nfp_date.replace(hour=13, minute=30, second=0)
    if abs(ts - nfp_time) <= blackout:
        return True

    # CPI check: around 12th-14th of month at 13:30
    for day in [12, 13, 14]:
        try:
            cpi_time = ts.replace(day=day, hour=13, minute=30, second=0)
            if abs(ts - cpi_time) <= blackout:
                return True
        except ValueError:
            pass

    # FOMC check: 8 times per year, typically around 19:00 UTC
    # Approximate: 3rd Wednesday of Jan, Mar, May, Jun, Jul, Sep, Nov, Dec
    fomc_months = [1, 3, 5, 6, 7, 9, 11, 12]
    if ts.month in fomc_months:
        # Find 3rd Wednesday
        first_day = ts.replace(day=1)
        days_until_wed = (2 - first_day.dayofweek) % 7
        third_wed = first_day + pd.Timedelta(days=days_until_wed + 14)
        fomc_time = third_wed.replace(hour=19, minute=0, second=0)
        if abs(ts - fomc_time) <= blackout:
            return True

    return False
