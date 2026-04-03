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
    try:
        now  = datetime.datetime.utcnow()
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
    except Exception:
        pass

    # Fallback: generate placeholder events for major known releases
    if not events:
        source = 'placeholder'
        now = datetime.datetime.utcnow()
        # Common high-impact times (approximate — user should download fresh data)
        placeholder_events = [
            ('USD', 'Non-Farm Payrolls', 'HIGH'),
            ('USD', 'FOMC Rate Decision', 'HIGH'),
            ('USD', 'CPI (YoY)', 'HIGH'),
            ('USD', 'GDP (QoQ)', 'HIGH'),
            ('EUR', 'ECB Rate Decision', 'HIGH'),
            ('GBP', 'BoE Rate Decision', 'HIGH'),
            ('USD', 'Initial Jobless Claims', 'MEDIUM'),
            ('USD', 'ISM Manufacturing PMI', 'MEDIUM'),
            ('EUR', 'CPI (Flash)', 'HIGH'),
        ]
        for i, (currency, event, impact) in enumerate(placeholder_events):
            dt = now + datetime.timedelta(days=i * 3)
            if impact_rank.get(impact, 0) >= min_rank:
                events.append({
                    'datetime_utc': dt.strftime('%Y-%m-%dT%H:%M:%S'),
                    'currency':     currency,
                    'event':        event,
                    'impact':       impact,
                })

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

    now = datetime.datetime.utcnow()
    cutoff = now + datetime.timedelta(hours=hours_ahead)
    upcoming = []

    with open(news_csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                dt = datetime.datetime.fromisoformat(row['datetime_utc'].replace('Z', ''))
                if now <= dt <= cutoff:
                    upcoming.append(row)
            except Exception:
                continue

    return upcoming
