"""
Historical news calendar — high-impact economic events.
Used to filter out trades that would have entered during news blackout periods.

Covers:
  - NFP: first Friday of each month at 8:30 ET (DST-aware)
  - CPI: real BLS release dates from 2020-2026 at 8:30 ET (DST-aware)
  - FOMC: real Fed rate-decision dates from 2020-2026 at 2:00 PM ET (DST-aware)

WHY the old version was wrong:
  1. NFP/CPI hardcoded at 13:30 UTC — only correct during EST (Nov-Mar).
     During EDT (Mar-Nov) the real release is 12:30 UTC, so the filter
     missed every summer release.
  2. CPI fired for any 12/13/14 of the month — triple-counts. Real CPI
     releases on ONE specific day per month, varying between 10-15.
  3. FOMC hardcoded at 19:00 UTC with "3rd Wednesday of selected months"
     — wrong time (real is 14:00 ET = 18 or 19 UTC depending on DST) and
     wrong dates (FOMC meetings follow a Fed-published schedule, not a
     3rd-Wed rule; e.g., Nov 2025 meeting was Nov 4-5, not the 3rd Wed).

CHANGED: April 2026 — DST-aware, real historical dates (audit bugs #8 + family #8)
"""

import pandas as pd
import pytz
import os
import json
from datetime import datetime, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
CALENDAR_PATH = os.path.join(_HERE, 'data', 'news_calendar.json')

# US Eastern timezone handle — used for EST/EDT-aware UTC conversion.
# Avoids hardcoding "13:30 UTC" which is wrong half the year.
_ET = pytz.timezone('US/Eastern')
_UTC = pytz.UTC

# ── Event release times (US Eastern Time) ────────────────────────────────────
# These are the local Eastern times published by the releasing agency.
# We convert to UTC on demand so DST transitions handle automatically.
NFP_TIME_ET  = (8, 30)   # 8:30 AM ET — BLS Employment Situation Report
CPI_TIME_ET  = (8, 30)   # 8:30 AM ET — BLS Consumer Price Index
FOMC_TIME_ET = (14, 0)   # 2:00 PM ET — Fed rate decision announcement

# ── Real historical CPI release dates (BLS schedule) ─────────────────────────
# Source: https://www.bls.gov/schedule/news_release/cpi.htm
# Each entry is (year, month, day) of the release date (not the reference month).
# The release is always at 8:30 AM ET.
CPI_RELEASE_DATES = {
    # 2020
    (2020, 1, 14), (2020, 2, 13), (2020, 3, 11), (2020, 4, 10), (2020, 5, 12),
    (2020, 6, 10), (2020, 7, 14), (2020, 8, 12), (2020, 9, 11), (2020, 10, 13),
    (2020, 11, 12), (2020, 12, 10),
    # 2021
    (2021, 1, 13), (2021, 2, 10), (2021, 3, 10), (2021, 4, 13), (2021, 5, 12),
    (2021, 6, 10), (2021, 7, 13), (2021, 8, 11), (2021, 9, 14), (2021, 10, 13),
    (2021, 11, 10), (2021, 12, 10),
    # 2022
    (2022, 1, 12), (2022, 2, 10), (2022, 3, 10), (2022, 4, 12), (2022, 5, 11),
    (2022, 6, 10), (2022, 7, 13), (2022, 8, 10), (2022, 9, 13), (2022, 10, 13),
    (2022, 11, 10), (2022, 12, 13),
    # 2023
    (2023, 1, 12), (2023, 2, 14), (2023, 3, 14), (2023, 4, 12), (2023, 5, 10),
    (2023, 6, 13), (2023, 7, 12), (2023, 8, 10), (2023, 9, 13), (2023, 10, 12),
    (2023, 11, 14), (2023, 12, 12),
    # 2024
    (2024, 1, 11), (2024, 2, 13), (2024, 3, 12), (2024, 4, 10), (2024, 5, 15),
    (2024, 6, 12), (2024, 7, 11), (2024, 8, 14), (2024, 9, 11), (2024, 10, 10),
    (2024, 11, 13), (2024, 12, 11),
    # 2025
    (2025, 1, 15), (2025, 2, 12), (2025, 3, 12), (2025, 4, 10), (2025, 5, 13),
    (2025, 6, 11), (2025, 7, 15), (2025, 8, 12), (2025, 9, 11), (2025, 10, 24),
    (2025, 11, 13), (2025, 12, 10),
    # 2026
    (2026, 1, 13), (2026, 2, 11), (2026, 3, 11), (2026, 4, 10), (2026, 5, 12),
    (2026, 6, 10), (2026, 7, 14), (2026, 8, 12), (2026, 9, 11), (2026, 10, 14),
    (2026, 11, 10), (2026, 12, 10),
}

# ── Real historical FOMC rate decision dates (Fed schedule) ──────────────────
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Each entry is (year, month, day) of the second day of the meeting
# (when the rate decision is announced at 2:00 PM ET).
FOMC_DECISION_DATES = {
    # 2020
    (2020, 1, 29), (2020, 3, 15),  # Mar 15 emergency cut (Sunday!)
    (2020, 4, 29), (2020, 6, 10), (2020, 7, 29), (2020, 9, 16),
    (2020, 11, 5), (2020, 12, 16),
    # 2021
    (2021, 1, 27), (2021, 3, 17), (2021, 4, 28), (2021, 6, 16),
    (2021, 7, 28), (2021, 9, 22), (2021, 11, 3), (2021, 12, 15),
    # 2022
    (2022, 1, 26), (2022, 3, 16), (2022, 5, 4), (2022, 6, 15),
    (2022, 7, 27), (2022, 9, 21), (2022, 11, 2), (2022, 12, 14),
    # 2023
    (2023, 2, 1), (2023, 3, 22), (2023, 5, 3), (2023, 6, 14),
    (2023, 7, 26), (2023, 9, 20), (2023, 11, 1), (2023, 12, 13),
    # 2024
    (2024, 1, 31), (2024, 3, 20), (2024, 5, 1), (2024, 6, 12),
    (2024, 7, 31), (2024, 9, 18), (2024, 11, 7), (2024, 12, 18),
    # 2025
    (2025, 1, 29), (2025, 3, 19), (2025, 5, 7), (2025, 6, 18),
    (2025, 7, 30), (2025, 9, 17), (2025, 11, 5), (2025, 12, 10),
    # 2026 (tentative — Fed publishes in August of prior year)
    (2026, 1, 28), (2026, 3, 18), (2026, 5, 6), (2026, 6, 17),
    (2026, 7, 29), (2026, 9, 16), (2026, 11, 4), (2026, 12, 16),
}


def _et_to_utc(year, month, day, hour, minute):
    """
    Convert a US Eastern local time to a UTC pandas Timestamp.

    Handles EST (UTC-5) and EDT (UTC-4) transitions automatically via
    pytz. Returns a tz-naive UTC Timestamp (matching the convention of
    the old code which used naive UTC everywhere).

    WHY: Old code hardcoded 13:30 UTC for NFP/CPI, which is only
    correct in EST (Nov-Mar). During EDT the real 8:30 ET is 12:30 UTC,
    so the filter was off by 1 hour for half the year.
    CHANGED: April 2026 — DST-aware time conversion (audit bug #8)
    """
    try:
        local_dt = datetime(year, month, day, hour, minute)
        # localize() attaches the EDT/EST offset correct for that specific date
        aware_et = _ET.localize(local_dt)
        # convert to UTC and drop tzinfo so it compares cleanly with
        # tz-naive pandas Timestamps used elsewhere in the codebase
        utc_dt = aware_et.astimezone(_UTC).replace(tzinfo=None)
        return pd.Timestamp(utc_dt)
    except Exception:
        # Fallback: assume EST (UTC-5) if localization fails
        return pd.Timestamp(datetime(year, month, day, hour + 5, minute))


def get_nfp_dates(start_year=2020, end_year=2026):
    """
    Generate NFP release datetimes as UTC pandas Timestamps.

    NFP releases on the **first Friday** of each month at **8:30 AM ET**,
    which is 12:30 UTC in EDT and 13:30 UTC in EST.

    WHY the first Friday: this is a published BLS rule, not a heuristic.
    It's stable and correct back to the 1940s.

    CHANGED: April 2026 — DST-aware via _et_to_utc (audit bug #8)
    """
    dates = []
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Find first Friday of the month
            first_day = datetime(year, month, 1)
            days_until_friday = (4 - first_day.weekday()) % 7
            friday_day = 1 + days_until_friday
            nfp_ts = _et_to_utc(year, month, friday_day, *NFP_TIME_ET)
            dates.append(nfp_ts)
    return dates


def is_news_blackout(timestamp, blackout_minutes=5):
    """
    Check if a timestamp falls within a news blackout period.

    Checks three event types:
      - NFP: first Friday of month at 8:30 AM ET (DST-aware)
      - CPI: real BLS release dates (hardcoded table 2020-2026) at 8:30 AM ET
      - FOMC: real Fed decision dates (hardcoded table 2020-2026) at 2:00 PM ET

    Parameters
    ----------
    timestamp : anything pandas can parse into a Timestamp
        The time to check (assumed UTC/naive).
    blackout_minutes : int, default 5
        Half-window in minutes. **NOTE: this is a HALF-window**, so the
        total blocked period is 2× this value. A trade at ts will be
        blocked if any event occurs within ``[ts - blackout_minutes,
        ts + blackout_minutes]``. This matches the old behavior for
        backward compatibility with existing backtests.

    Returns
    -------
    bool
        True if the timestamp is within any news event's blackout window.

    WHY: Old code hardcoded UTC times (13:30 for NFP/CPI, 19:00 for FOMC)
    which were only correct during EST, and used a "12/13/14 of month"
    heuristic for CPI that triple-counted, and a "3rd Wednesday" rule
    for FOMC that was wrong ~30% of the time. Now uses real published
    dates + DST-aware time conversion.
    CHANGED: April 2026 — real dates, DST-aware (audit bugs #8 + family #8)
    """
    ts = pd.to_datetime(timestamp)
    if ts is pd.NaT:
        return False
    # Drop tz info if present — our event timestamps are tz-naive UTC
    if ts.tz is not None:
        ts = ts.tz_convert('UTC').tz_localize(None)

    blackout = pd.Timedelta(minutes=blackout_minutes)

    year = ts.year
    month = ts.month

    # ── NFP check: first Friday of the month at 8:30 ET ──────────────────
    try:
        first_day = datetime(year, month, 1)
        days_until_friday = (4 - first_day.weekday()) % 7
        nfp_day = 1 + days_until_friday
        nfp_ts = _et_to_utc(year, month, nfp_day, *NFP_TIME_ET)
        if abs(ts - nfp_ts) <= blackout:
            return True
    except Exception:
        pass

    # ── CPI check: real BLS release date for this month ─────────────────
    # WHY: Old code fired for 12/13/14 of every month — triple-counted.
    #      Real CPI releases on ONE specific day per month varying 10-15.
    # CHANGED: April 2026 — use hardcoded BLS schedule (audit bug #8)
    for (cpi_y, cpi_m, cpi_d) in CPI_RELEASE_DATES:
        if cpi_y == year and cpi_m == month:
            cpi_ts = _et_to_utc(cpi_y, cpi_m, cpi_d, *CPI_TIME_ET)
            if abs(ts - cpi_ts) <= blackout:
                return True
            break  # one CPI release per month

    # ── FOMC check: real Fed decision date for this month ───────────────
    # WHY: Old code used "3rd Wednesday of selected months" — wrong ~30%
    #      of the time. Real FOMC schedule comes from the Fed.
    # CHANGED: April 2026 — use hardcoded Fed schedule (audit bug #8)
    for (fomc_y, fomc_m, fomc_d) in FOMC_DECISION_DATES:
        if fomc_y == year and fomc_m == month:
            fomc_ts = _et_to_utc(fomc_y, fomc_m, fomc_d, *FOMC_TIME_ET)
            if abs(ts - fomc_ts) <= blackout:
                return True
            # Don't break — some months have no FOMC, others might have
            # more than one (rare but possible for emergency meetings).

    return False


def get_event_count_by_year(start_year=2020, end_year=2026):
    """
    Diagnostic helper: return counts of NFP/CPI/FOMC events per year.
    Used by the verification script to confirm the tables are populated.

    CHANGED: April 2026 — diagnostic helper for phase 6 verification
    """
    counts = {}
    for y in range(start_year, end_year + 1):
        nfp_count = 12  # first Friday rule — always 12 per year
        cpi_count = sum(1 for (cy, cm, cd) in CPI_RELEASE_DATES if cy == y)
        fomc_count = sum(1 for (fy, fm, fd) in FOMC_DECISION_DATES if fy == y)
        counts[y] = {'nfp': nfp_count, 'cpi': cpi_count, 'fomc': fomc_count}
    return counts
