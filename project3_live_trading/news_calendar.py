"""
News Calendar — downloads high-impact economic events for the EA news filter.

Saves a CSV file that the MT5 EA and Tradovate bot read before each entry
to skip trading around major economic releases.

CSV columns: datetime_utc, currency, event, impact
"""

import os
import csv
import json
import datetime
import urllib.request
import urllib.error

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(_HERE, 'outputs', 'news_calendar.csv')


def download_news_calendar(
    days_ahead=30,
    currencies=None,
    min_impact='HIGH',
    output_path=None,
):
    """
    Download high-impact economic events.

    Tries multiple sources:
    1. ForexFactory-style JSON (if accessible)
    2. FCS API (free tier)
    3. Generates a placeholder file if all sources fail

    Parameters
    ----------
    days_ahead    : int   — how many days of events to fetch
    currencies    : list  — filter by currency e.g. ['USD', 'XAU'] (None = all)
    min_impact    : str   — 'HIGH', 'MEDIUM', or 'LOW' (inclusive)
    output_path   : str   — where to save the CSV (default: outputs/news_calendar.csv)

    Returns
    -------
    dict with 'events' list, 'count', 'source', 'output_path'
    """
    if output_path is None:
        output_path = DEFAULT_OUTPUT
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if currencies is None:
        currencies = ['USD', 'EUR', 'GBP', 'JPY', 'XAU', 'XAG']

    impact_rank = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
    min_rank    = impact_rank.get(min_impact, 3)

    events = []
    source = 'none'

    # Try fetching from a free economic calendar API
    # WHY (Phase 34 Fix 1): Old code silently accepted days_ahead > 7
    #      but the URL only returns this week's events (ff_calendar_thisweek.json).
    #      Users expecting a 30-day forecast got 0-7 days with no warning.
    #      Log a loud warning when the ask exceeds the URL's window so
    #      the user knows to re-run the download script weekly or switch
    #      to a paid API that supports longer ranges.
    # CHANGED: April 2026 — Phase 34 Fix 1 — warn on days_ahead > 7
    #          (audit Part C HIGH #45)
    if days_ahead > 7:
        print(
            f"[NEWS_CALENDAR] WARNING: days_ahead={days_ahead} but the "
            f"ForexFactory URL only returns this week's events (max 7 days). "
            f"Only events up to 7 days from now will be available. Run this "
            f"download weekly to keep the calendar current, or switch to a "
            f"paid API that supports longer windows."
        )

    try:
        # WHY: datetime.utcnow() returns a naive datetime and is deprecated
        #      in Python 3.12+. datetime.now(timezone.utc) returns a
        #      timezone-aware UTC datetime which is safer.
        # CHANGED: April 2026 — timezone-aware UTC (Phase 19c)
        now  = datetime.datetime.now(datetime.timezone.utc)
        end  = now + datetime.timedelta(days=days_ahead)
        url  = (
            f"https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        )
        req  = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode('utf-8'))
        for item in raw:
            try:
                dt_str   = item.get('date', '') or item.get('datetime', '')
                currency = item.get('country', '').upper()
                title    = item.get('title', item.get('event', ''))
                impact_s = (item.get('impact', '') or '').upper()
                if impact_s not in impact_rank:
                    impact_s = 'MEDIUM'
                if impact_rank.get(impact_s, 0) < min_rank:
                    continue
                events.append({
                    'datetime_utc': dt_str,
                    'currency':     currency,
                    'event':        title,
                    'impact':       impact_s,
                })
            except Exception:
                continue
        source = 'ForexFactory JSON'
    # WHY (Phase 34 Fix 2): Old code used `except Exception: pass`,
    #      swallowing every failure category indistinguishably. Network
    #      timeout, SSL cert error, JSON parse error, HTTP 429 rate
    #      limit — user had no log of what went wrong when events came
    #      back empty. Log the exception type and message before
    #      falling through to the no-events fallback so operators can
    #      diagnose fetch failures.
    # CHANGED: April 2026 — Phase 34 Fix 2 — log exception details
    #          (audit Part C HIGH #46)
    except urllib.error.HTTPError as e:
        print(f"[NEWS_CALENDAR] HTTP {e.code} fetching calendar: {e.reason}")
    except urllib.error.URLError as e:
        print(f"[NEWS_CALENDAR] URL/network error fetching calendar: {e.reason}")
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[NEWS_CALENDAR] JSON parse error in calendar response: {e}")
    except Exception as e:
        # Last-resort catch so import/caller never crashes
        print(f"[NEWS_CALENDAR] Unexpected error fetching calendar: "
              f"{type(e).__name__}: {e}")

    # WHY: Old fallback generated fictional events with datetime.utcnow()+timedelta
    #      offsets when the real calendar API failed. Those fake events had real
    #      timestamps and would silently block trades during fabricated news windows.
    #      A user who never noticed the API was down could lose trades to blackouts
    #      that never existed. Removing them and surfacing a loud warning instead is
    #      strictly safer — the user knows the calendar is unavailable.
    # CHANGED: April 2026 — remove fake placeholder events (audit CRITICAL)
    if not events:
        source = 'unavailable'
        print(
            "[NEWS_CALENDAR] WARNING: Could not fetch news calendar from any source. "
            "No blackout windows will be applied. Download a fresh calendar manually "
            "or ensure network access to the calendar API before running live."
        )

    # Write CSV
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['datetime_utc', 'currency', 'event', 'impact'])
        writer.writeheader()
        writer.writerows(events)

    return {
        'events':      events,
        'count':       len(events),
        'source':      source,
        'output_path': output_path,
    }


def get_upcoming_events(hours_ahead=2, news_csv_path=None):
    """
    Read saved news calendar and return events in the next N hours.
    Used by the EA verifier and panel to show upcoming risk events.
    """
    if news_csv_path is None:
        news_csv_path = DEFAULT_OUTPUT
    if not os.path.exists(news_csv_path):
        return []

    # WHY: datetime.utcnow() deprecated in 3.12+. Use timezone-aware UTC
    #      throughout — both `now` and the parsed `dt` from the CSV must
    #      be aware-or-both-naive for comparisons to work. We attach UTC
    #      tzinfo to `dt` after parsing the ISO string.
    # CHANGED: April 2026 — timezone-aware UTC + consistent comparison (Phase 19c)
    now = datetime.datetime.now(datetime.timezone.utc)
    cutoff = now + datetime.timedelta(hours=hours_ahead)
    upcoming = []

    with open(news_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                dt = datetime.datetime.fromisoformat(row['datetime_utc'].replace('Z', ''))
                # Attach UTC tz so the comparison against `now` works
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                if now <= dt <= cutoff:
                    upcoming.append(row)
            except Exception:
                continue

    return upcoming
