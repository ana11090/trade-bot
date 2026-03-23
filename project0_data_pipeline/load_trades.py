# ==============================================================================
# project0_data_pipeline/load_trades.py
# ------------------------------------------------------------------------------
# PROJECT:  Project 0 — Data Pipeline
# PURPOSE:  This module does all the actual data processing work.
#           It is called by the GUI (main_app.py) — not run directly.
#
# INPUT:    A .txt or .csv file exported from Myfxbook.
#           Expected columns:
#             Open Date, Close Date, Symbol, Action, Lots, SL, TP,
#             Open Price, Close Price, Pips, Profit, Duration (DDHHMMSS), Change %
#           Date format in the file: MMDDYYYY HHMM  (e.g. 03102026 1024)
#
# OUTPUT:   - A cleaned file called trades_clean.csv saved next to the input file
#           - A Python dictionary of summary statistics returned to the GUI
#
# HOW IT IS USED:
#   The GUI (main_app.py) calls run_pipeline(file_path) and gets back a summary.
#   All print() output from here is captured by the GUI log panel automatically.
# ==============================================================================

import pandas as pd      # pandas handles loading and cleaning the data table
import os                # os is used to build file paths


def run_pipeline(file_path):
    # -------------------------------------------------------------------------
    # This is the main entry point. The GUI calls this function with the full
    # path to the file the user selected. It runs all 7 steps and returns a
    # dictionary of results for the GUI to display as summary cards.
    # -------------------------------------------------------------------------

    print("=" * 56)
    print(" TRADING BOT — DATA PIPELINE — PROJECT 0")
    print("=" * 56)
    print("")

    # --------------------------------------------------------------------------
    # STEP 1: Load the raw file into a data table
    # --------------------------------------------------------------------------
    print("STEP 1 — Loading file...")
    print(f"  File: {file_path}")
    print("")

    # try to read the file — wrap in try/except so we get a clear error if it fails
    try:
        raw_trade_data = pd.read_csv(
            file_path,              # the file path the user selected in the GUI
            sep=",",                # columns are separated by commas
            skipinitialspace=True,  # remove spaces right after commas so values are clean
            skip_blank_lines=True,  # skip any completely empty lines in the file
            engine="python"         # the python engine handles unusual formatting better
        )
    except FileNotFoundError:
        # the file no longer exists at the path that was selected
        print(f"ERROR: The file was not found at: {file_path}")
        print("Make sure the file has not been moved or deleted, then try again.")
        raise   # pass the error up to the GUI so it can show the failure state

    except PermissionError:
        # another program (e.g. Excel) has the file locked and open
        print("ERROR: Could not open the file — it may be open in another program.")
        print("Close Excel or any other program that has the file open, then try again.")
        raise

    except Exception as unexpected_error:
        # something else went wrong that we did not anticipate
        print("ERROR: Could not read the file.")
        print(f"Reason: {unexpected_error}")
        print("Check that the file is a valid .txt or .csv export from Myfxbook.")
        raise

    # count how many rows and columns were loaded
    total_rows_loaded    = len(raw_trade_data)              # number of data rows
    total_columns_loaded = len(raw_trade_data.columns)      # number of columns
    print(f"  Loaded {total_rows_loaded} rows and {total_columns_loaded} columns.")
    print("")

    # print the first 5 rows so the user can visually verify the data looks correct
    print("  First 5 rows of raw data:")
    print(raw_trade_data.head().to_string())    # .to_string() prevents pandas from truncating wide tables
    print("")

    # print the column names exactly as they appear in the file
    print("  Column names as read from file:")
    print(f"  {list(raw_trade_data.columns)}")
    print("")

    # --------------------------------------------------------------------------
    # STEP 2: Clean the column names
    # --------------------------------------------------------------------------
    print("STEP 2 — Cleaning column names...")

    # remove any invisible whitespace characters from the start/end of each column name
    raw_trade_data.columns = raw_trade_data.columns.str.strip()

    print(f"  Column names after cleaning: {list(raw_trade_data.columns)}")
    print("")

    # --------------------------------------------------------------------------
    # STEP 3: Remove completely empty rows
    # --------------------------------------------------------------------------
    print("STEP 3 — Removing empty rows...")

    row_count_before_drop = len(raw_trade_data)        # save the row count before we remove anything
    raw_trade_data = raw_trade_data.dropna(how="all")  # drop rows where EVERY column is blank/NaN
    row_count_after_drop = len(raw_trade_data)         # count how many rows remain
    empty_rows_removed   = row_count_before_drop - row_count_after_drop   # how many were dropped

    print(f"  Rows before: {row_count_before_drop}")
    print(f"  Rows after:  {row_count_after_drop}")
    print(f"  Empty rows removed: {empty_rows_removed}")
    print("")

    # --------------------------------------------------------------------------
    # STEP 4: Parse the date columns into proper datetime objects
    # --------------------------------------------------------------------------
    print("STEP 4 — Parsing date columns...")
    print("  Expected format: MMDDYYYY HHMM  (e.g. 03102026 1024 = March 10 2026, 10:24)")

    # this format string tells pandas how to decode the date+time text
    # %m = 2-digit month, %d = 2-digit day, %Y = 4-digit year, %H = hour, %M = minute
    date_format_string = "%m%d%Y %H%M"

    # stop and report clearly if the Open Date column is missing entirely
    if "Open Date" not in raw_trade_data.columns:
        print("ERROR: The column 'Open Date' was not found in the file.")
        print(f"Available columns: {list(raw_trade_data.columns)}")
        raise ValueError("Missing required column: Open Date")

    # stop and report clearly if the Close Date column is missing entirely
    if "Close Date" not in raw_trade_data.columns:
        print("ERROR: The column 'Close Date' was not found in the file.")
        print(f"Available columns: {list(raw_trade_data.columns)}")
        raise ValueError("Missing required column: Close Date")

    # convert Open Date from text to a real datetime — unparseable values become NaT (blank)
    raw_trade_data["Open Date"] = pd.to_datetime(
        raw_trade_data["Open Date"].astype(str).str.strip(),   # strip whitespace before parsing
        format=date_format_string,                             # decode using our format
        errors="coerce"                                        # bad values become NaT, not a crash
    )

    # convert Close Date from text to a real datetime — same approach
    raw_trade_data["Close Date"] = pd.to_datetime(
        raw_trade_data["Close Date"].astype(str).str.strip(),  # strip whitespace before parsing
        format=date_format_string,                             # same format
        errors="coerce"                                        # bad values become NaT
    )

    # count how many values could not be parsed (NaT = Not a Time = blank date)
    unparsed_open_date_count  = raw_trade_data["Open Date"].isna().sum()
    unparsed_close_date_count = raw_trade_data["Close Date"].isna().sum()

    # warn the user if any dates were unreadable
    if unparsed_open_date_count > 0:
        print(f"  WARNING: {unparsed_open_date_count} 'Open Date' values could not be read and were left blank.")
    if unparsed_close_date_count > 0:
        print(f"  WARNING: {unparsed_close_date_count} 'Close Date' values could not be read and were left blank.")

    print("  Date columns parsed successfully.")
    print("")

    # --------------------------------------------------------------------------
    # STEP 5: Convert numeric columns to actual numbers
    # --------------------------------------------------------------------------
    print("STEP 5 — Converting numeric columns...")

    # every column in this list should contain a number — convert it from text
    numeric_column_names = [
        "Lots",
        "SL",
        "TP",
        "Open Price",
        "Close Price",
        "Pips",
        "Profit",
        "Change %"
    ]

    # go through each column name and try to convert it
    for column_name in numeric_column_names:
        if column_name in raw_trade_data.columns:
            # pd.to_numeric converts text to number; errors="coerce" sets bad values to NaN
            raw_trade_data[column_name] = pd.to_numeric(
                raw_trade_data[column_name],
                errors="coerce"
            )
            print(f"  Converted '{column_name}' to number.")
        else:
            # column not in file — warn but keep going, it might not be critical
            print(f"  WARNING: Column '{column_name}' not found in file — skipping.")

    print("")

    # --------------------------------------------------------------------------
    # STEP 6: Build summary statistics
    # --------------------------------------------------------------------------
    print("STEP 6 — Building summary...")

    total_trade_count = len(raw_trade_data)   # total number of trade rows in the cleaned data
    print(f"  Total trades: {total_trade_count}")

    # calculate the date range from earliest to latest open date
    if raw_trade_data["Open Date"].notna().any():           # only do this if at least one date is valid
        earliest_trade_date = raw_trade_data["Open Date"].min()   # the oldest trade
        latest_trade_date   = raw_trade_data["Open Date"].max()   # the newest trade
        earliest_formatted  = earliest_trade_date.strftime("%d %b %Y %H:%M")   # e.g. "09 Mar 2026 19:18"
        latest_formatted    = latest_trade_date.strftime("%d %b %Y %H:%M")
        date_range_text     = f"{earliest_formatted}  →  {latest_formatted}"
        print(f"  Date range: {date_range_text}")
    else:
        date_range_text = "Could not determine (no valid dates found)"
        print(f"  Date range: {date_range_text}")

    # collect all unique symbol names that appear in the data
    if "Symbol" in raw_trade_data.columns:
        unique_symbols_list = raw_trade_data["Symbol"].dropna().unique().tolist()   # unique non-blank symbols
        symbols_text        = ", ".join(unique_symbols_list)                        # join as readable text
        print(f"  Symbols: {symbols_text}")
    else:
        symbols_text = "Symbol column not found"
        print(f"  Symbols: {symbols_text}")

    # calculate win rate — a winning trade has Profit > 0
    if "Profit" in raw_trade_data.columns and raw_trade_data["Profit"].notna().any():
        winning_trade_rows  = raw_trade_data[raw_trade_data["Profit"] > 0]      # filter to winning rows only
        winning_trade_count = len(winning_trade_rows)                            # count the winners
        win_rate_percentage = (winning_trade_count / total_trade_count) * 100    # win% out of all trades
        total_profit_value  = raw_trade_data["Profit"].sum()                     # total net profit
        print(f"  Winning trades: {winning_trade_count} out of {total_trade_count}")
        print(f"  Win rate: {win_rate_percentage:.1f}%")
        print(f"  Total profit: {total_profit_value:,.2f}")
    else:
        winning_trade_count = 0
        win_rate_percentage = 0.0
        total_profit_value  = 0.0
        print("  Win rate: could not calculate (Profit column missing or empty)")

    print("")

    # --------------------------------------------------------------------------
    # STEP 7: Save the cleaned data to trades_clean.csv
    # --------------------------------------------------------------------------
    print("STEP 7 — Saving cleaned file...")

    input_file_folder = os.path.dirname(file_path)                           # folder that contains the input file
    output_file_path  = os.path.join(input_file_folder, "trades_clean.csv")  # output file goes in the same folder

    try:
        raw_trade_data.to_csv(
            output_file_path,   # destination path for the cleaned CSV
            index=False         # do not write pandas row numbers as a column
        )
    except PermissionError:
        print("ERROR: Could not save — trades_clean.csv may be open in another program.")
        print("Close it and try again.")
        raise
    except Exception as save_error:
        print("ERROR: Could not save the cleaned file.")
        print(f"Reason: {save_error}")
        raise

    print(f"  Saved to: {output_file_path}")
    print("")
    print("=" * 56)
    print(" ALL DONE — pipeline completed successfully.")
    print("=" * 56)

    # --------------------------------------------------------------------------
    # Return the summary as a dictionary so the GUI can show it as cards
    # --------------------------------------------------------------------------
    summary_dictionary = {
        "total_trades":   total_trade_count,              # integer  — total number of trades
        "winning_trades": winning_trade_count,            # integer  — number of winning trades
        "win_rate":       round(win_rate_percentage, 1),  # float    — win percentage (1 decimal)
        "total_profit":   round(total_profit_value, 2),   # float    — net profit (2 decimals)
        "date_range":     date_range_text,                # string   — readable date range
        "symbols":        symbols_text,                   # string   — comma-separated symbol names
        "output_path":    output_file_path,               # string   — full path to the saved CSV
    }

    return summary_dictionary   # the GUI reads this to populate the summary cards
