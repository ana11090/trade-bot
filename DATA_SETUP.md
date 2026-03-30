# Data Setup Guide

## Downloaded Historical Data

Complete XAUUSD tick data has been downloaded and is stored at:
```
D:\traiding data\xauusd\
├── ticks\          (51 GB - 258 CSV files)
│   ├── 2005\       (12 months)
│   ├── 2006\       (12 months)
│   ├── ...
│   ├── 2025\       (12 months)
│   └── 2026\       (3 months: Jan-Mar)
└── timeframes\     (for aggregated data)
```

## Data Coverage

- **Period:** 2005-01-01 to 2026-03-30
- **Total:** 21+ years of complete tick data
- **Files:** 258 monthly CSV files
- **Size:** 51 GB
- **Symbol:** XAUUSD (Gold vs USD)

## Tick Data Format

Each CSV file contains:
- `timestamp`: Exact time with millisecond precision
- `ask`: Ask price (sell price)
- `bid`: Bid price (buy price)
- `mid`: Mid price (average of ask/bid)
- `spread`: Spread (ask - bid)
- `volume`: Trading volume

## Using the Data

### Option 1: Direct Access
```python
import pandas as pd

# Load a specific month
df = pd.read_csv(r'D:\traiding data\xauusd\ticks\2024\ticks_2024_01.csv')
print(f"Loaded {len(df):,} ticks")
```

### Option 2: Use Config
```python
from config import TICK_DATA_PATH
import os
import pandas as pd

# Load all ticks for a year
year = 2024
year_path = os.path.join(TICK_DATA_PATH, str(year))
all_ticks = []

for month in range(1, 13):
    file_path = os.path.join(year_path, f"ticks_{year}_{month:02d}.csv")
    if os.path.exists(file_path):
        df = pd.read_csv(file_path)
        all_ticks.append(df)

yearly_data = pd.concat(all_ticks, ignore_index=True)
```

### Option 3: Load Date Range
```python
from config import TICK_DATA_PATH
import pandas as pd
from datetime import datetime
import os

def load_ticks(start_date, end_date):
    """Load ticks for a date range"""
    ticks = []

    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)

    # Iterate through months in range
    current = start
    while current <= end:
        file_path = os.path.join(
            TICK_DATA_PATH,
            str(current.year),
            f"ticks_{current.year}_{current.month:02d}.csv"
        )

        if os.path.exists(file_path):
            df = pd.read_csv(file_path)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Filter to date range
            mask = (df['timestamp'] >= start) & (df['timestamp'] <= end)
            ticks.append(df[mask])

        # Move to next month
        if current.month == 12:
            current = pd.Timestamp(year=current.year + 1, month=1, day=1)
        else:
            current = pd.Timestamp(year=current.year, month=current.month + 1, day=1)

    return pd.concat(ticks, ignore_index=True) if ticks else pd.DataFrame()

# Example: Load Q1 2024
df = load_ticks('2024-01-01', '2024-03-31')
print(f"Loaded {len(df):,} ticks for Q1 2024")
```

## Creating Timeframes

To create aggregated timeframes (M5, H1, D1, etc.) from tick data:

```python
import pandas as pd

def resample_to_timeframe(tick_df, freq):
    """
    Resample tick data to OHLCV timeframe

    freq: '5min', '15min', '1H', '4H', 'D', 'W', 'M'
    """
    df = tick_df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df.set_index('timestamp', inplace=True)

    # Resample
    ohlc = df['mid'].resample(freq).ohlc()
    volume = df['volume'].resample(freq).sum()

    result = pd.DataFrame({
        'timestamp': ohlc.index,
        'open': ohlc['open'],
        'high': ohlc['high'],
        'low': ohlc['low'],
        'close': ohlc['close'],
        'volume': volume.values
    })

    return result.dropna()

# Example: Create H1 from tick data
tick_data = load_ticks('2024-01-01', '2024-01-31')
h1_data = resample_to_timeframe(tick_data, '1H')
h1_data.to_csv(r'D:\traiding data\xauusd\timeframes\xauusd_H1_2024_01.csv', index=False)
```

## Git Repository

The code is stored in git, but the data files are NOT (too large).

- **Code:** https://github.com/ana11090/trade-bot
- **Data location:** `D:\traiding data\xauusd\` (local only)
- **Config:** `config.py` points to data location

## Important Notes

1. **Data is NOT in git** - The 51 GB of tick data is stored locally and excluded via .gitignore
2. **Config file** - Use `config.py` to access data paths consistently
3. **Timeframes** - Create aggregated timeframes as needed from tick data
4. **Memory** - Loading all ticks at once may require 16+ GB RAM. Load in chunks for large date ranges
5. **Performance** - Consider creating aggregated timeframes and saving them for faster access

## Next Steps

1. Create aggregated timeframes (M5, H1, D1, etc.) as needed
2. Use the data with project1_reverse_engineering analysis pipeline
3. Run backtests with project2_backtesting
4. Develop and test trading strategies
