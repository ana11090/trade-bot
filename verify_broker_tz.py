"""verify_broker_tz.py — confirm the broker's IANA timezone before refactoring.

Spot-check the broker-local -> UTC conversion against winter (EET +2) and
summer (EEST +3) samples. If the zone is correct, the Jan and Jul local-hour
distributions differ by 1, but the UTC hours align.

Usage:
    python verify_broker_tz.py --csv path/to/H4.csv
    python verify_broker_tz.py --csv path/to/H4.csv --tz Europe/Athens
    python verify_broker_tz.py --csv path/to/H4.csv --tz Europe/Bucharest --sample 14
"""
import argparse, pandas as pd
from zoneinfo import ZoneInfo
ap=argparse.ArgumentParser()
ap.add_argument('--csv',required=True); ap.add_argument('--tz',default='Europe/Athens')
ap.add_argument('--sample',type=int,default=14)
a=ap.parse_args()
df=pd.read_csv(a.csv); df['timestamp']=pd.to_datetime(df['timestamp'])
z=ZoneInfo(a.tz)
df['utc']=df['timestamp'].dt.tz_localize(z,ambiguous='NaT',nonexistent='NaT').dt.tz_convert('UTC').dt.tz_localize(None)
df['m']=df['timestamp'].dt.month
print("broker-local tz:",a.tz)
print("local H4 hours :",sorted(df['timestamp'].dt.hour.unique().tolist()))
print("UTC H4 hours   :",sorted(df['utc'].dt.hour.dropna().unique().astype(int).tolist()))
print()
print("Winter sample (Jan) local->UTC:")
for _,r in df[df.m==1].head(4).iterrows(): print("  ",r['timestamp'],"->",r['utc'])
print("Summer sample (Jul) local->UTC:")
for _,r in df[df.m==7].head(4).iterrows(): print("  ",r['timestamp'],"->",r['utc'])
