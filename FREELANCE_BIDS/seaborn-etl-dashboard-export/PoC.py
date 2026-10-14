#!/usr/bin/env python3
"""
PoC: Python ETL for Seaborn dashboard export
=============================================
Freelancer job: "Python ETL for Seaborn dashboard export - Python (v2 #108)"
Client wants: a Python ETL pipeline that takes raw tabular data, cleans it,
and exports a set of Seaborn charts (a dashboard) as image files.

This PoC implements that end-to-end, with no project data assumed:
  - Extract:  read a CSV (any CSV with 1 date column + >=2 numeric columns,
              auto-detected). Ships with a generated sample CSV so it runs
              out of the box.
  - Transform: drop bad rows, coerce types, fill gaps, compute rolling
               means and period-over-period deltas.
  - Load:      render a 4-panel Seaborn dashboard (trend line, bar chart,
               heatmap, rolling-avg line) and export PNG + PDF to ./out/.

Runnable as-is:
    python PoC.py                 # uses the bundled sample CSV
    python PoC.py mydata.csv      # uses your CSV (needs a date col + 2 numeric cols)
    python PoC.py --selftest      # deterministic offline checks, exit 0/1
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import os
import sys
import tempfile
from dataclasses import dataclass

import pandas as pd

try:
    import matplotlib
    matplotlib.use("Agg")  # headless: files only, no window
    import matplotlib.pyplot as plt
    import seaborn as sns
    HAVE_PLOTTING = True
except Exception:  # pragma: no cover - plotting libs optional for --selftest
    HAVE_PLOTTING = False

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")

# --------------------------------------------------------------------------
# EXTRACT
# --------------------------------------------------------------------------

DATE_HINTS = ("date", "time", "day", "week", "month", "timestamp", "period")
SKIP_COLS = ("id", "index", "idx", "uid", "name", "label", "category")


def _looks_date(v) -> bool:
    s = str(v).strip()
    if not s:
        return False
    # ISO-ish or US-ish dates, or month/year tokens
    for pat in (r"\d{4}-\d{2}-\d{2}", r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
                r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"):
        import re
        if re.search(pat, s, re.I):
            return True
    return False


def detect_columns(df: pd.DataFrame) -> tuple[str | None, list[str]]:
    """Find the date column (best guess) and usable numeric columns."""
    date_col = None
    for col in df.columns:
        low = str(col).lower()
        if any(h in low for h in DATE_HINTS):
            if df[col].map(_looks_date).mean() >= 0.5:
                date_col = col
                break
    if date_col is None:  # fallback: first column whose values look like dates
        for col in df.columns:
            try:
                if df[col].dropna().map(_looks_date).mean() >= 0.7:
                    date_col = col
                    break
            except Exception:
                continue
    numeric = []
    for col in df.columns:
        if col == date_col or str(col).lower() in SKIP_COLS:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().mean() >= 0.5:
            numeric.append(col)
    return date_col, numeric


def extract(path: str) -> pd.DataFrame:
    """Read the CSV, return raw frame. Raises on unreadable file."""
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    if df.empty:
        raise ValueError("CSV has no rows")
    return df


# --------------------------------------------------------------------------
# TRANSFORM
# --------------------------------------------------------------------------

@dataclass
class CleanData:
    date_col: str
    metric_cols: list[str]
    rows: int
    dropped: int
    frame: pd.DataFrame  # date-sorted, one row per period


def transform(raw: pd.DataFrame) -> CleanData:
    date_col, numeric = detect_columns(raw)
    if date_col is None or len(numeric) < 1:
        raise ValueError(
            "Could not find a date column + at least one numeric column. "
            f"Columns: {list(raw.columns)}")
    work = raw.copy()
    before = len(work)
    work[date_col] = pd.to_datetime(work[date_col], errors="coerce")
    work = work.dropna(subset=[date_col])
    work = work.drop_duplicates(subset=[date_col], keep="last")
    for c in numeric:
        work[c] = pd.to_numeric(work[c], errors="coerce")
    work = work.sort_values(date_col).reset_index(drop=True)
    # fill interior gaps per metric with interpolation, edges with ffill/bfill
    for c in numeric:
        work[c] = work[c].interpolate(limit_direction="both")
    dropped = before - len(work)
    return CleanData(date_col=date_col, metric_cols=numeric,
                     rows=len(work), dropped=dropped, frame=work)


def enrich(cd: CleanData) -> pd.DataFrame:
    """Add rolling means + period-over-period deltas for the dashboard."""
    out = cd.frame.copy()
    primary = cd.metric_cols[0]
    out[f"rolling_7_{primary}"] = out[primary].rolling(7, min_periods=1).mean()
    if len(out) > 1:
        out[f"delta_{primary}"] = out[primary].pct_change().fillna(0.0)
    return out


# --------------------------------------------------------------------------
# LOAD (export)
# --------------------------------------------------------------------------

def export_dashboard(cd: CleanData, out_dir: str = OUT_DIR,
                     title: str = "Data dashboard") -> dict:
    """Render the 4-panel Seaborn dashboard; write PNG + PDF. Returns paths."""
    if not HAVE_PLOTTING:
        raise RuntimeError("matplotlib/seaborn not installed (pip install seaborn matplotlib)")
    os.makedirs(out_dir, exist_ok=True)
    enr = enrich(cd)
    primary = cd.metric_cols[0]

    sns.set_theme(style="whitegrid", context="talk")
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(title, fontsize=18)

    # 1. Trend line (up to 4 series)
    ax = axes[0][0]
    for c in cd.metric_cols[:4]:
        ax.plot(enr[cd.date_col].dt.strftime("%Y-%m"), enr[c], label=c, lw=2)
    ax.set_title("Trend over time")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=45, labelsize=7)

    # 2. Bar chart: last 12 periods
    ax = axes[0][1]
    tail = enr.tail(12)
    ax.bar(tail[cd.date_col].dt.strftime("%Y-%m"), tail[primary], color="#2c7fb8")
    ax.set_title(f"{primary} - last 12 periods")
    ax.tick_params(axis="x", rotation=45, labelsize=7)

    # 3. Heatmap: correlation of the numeric metrics
    ax = axes[1][0]
    sns.heatmap(enr[cd.metric_cols].corr(), annot=True, fmt=".2f",
                cmap="coolwarm", center=0, ax=ax,
                linewidths=0.5, linecolor="white")
    ax.set_title("Metric correlation")

    # 4. Rolling average vs raw
    ax = axes[1][1]
    ax.plot(enr[cd.date_col].dt.strftime("%Y-%m"), enr[primary],
            alpha=0.35, lw=1, label=primary)
    ax.plot(enr[cd.date_col].dt.strftime("%Y-%m"), enr[f"rolling_7_{primary}"],
            color="#d7301f", lw=2.5, label="7-period rolling avg")
    ax.set_title("Raw vs rolling average")
    ax.legend(fontsize=8)
    ax.tick_params(axis="x", rotation=45, labelsize=7)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    png_path = os.path.join(out_dir, "dashboard.png")
    pdf_path = os.path.join(out_dir, "dashboard.pdf")
    fig.savefig(png_path, dpi=150, bbox_inches="tight")
    fig.savefig(pdf_path, bbox_inches="tight")
    plt.close(fig)
    # also export the clean table for the client's records
    csv_path = os.path.join(out_dir, "clean_data.csv")
    enr.to_csv(csv_path, index=False)
    return {"png": png_path, "pdf": pdf_path, "csv": csv_path}


# --------------------------------------------------------------------------
# SAMPLE DATA (bundled so the PoC runs out of the box)
# --------------------------------------------------------------------------

def make_sample_csv(path: str) -> None:
    """Deterministic 52-week sample: two product-line revenue series + noise.

    Dates are generated with real calendar arithmetic (Monday of each week),
    so every value is a valid date. Two interior cells are nulled on purpose
    to exercise the transform's gap-fill path.
    """
    import random
    from datetime import date, timedelta
    random.seed(42)
    base = date(2025, 1, 6)  # a Monday
    rows = []
    base_a, base_b = 4200.0, 2900.0
    for w in range(52):
        season_a = 1 + 0.25 * math.sin(w / 52 * 2 * math.pi - 1.2)
        season_b = 1 + 0.18 * math.cos(w / 52 * 2 * math.pi + 0.5)
        growth = 1 + w * 0.004
        a = base_a * season_a * growth + random.uniform(-180, 180)
        b = base_b * season_b * growth + random.uniform(-120, 120)
        # a couple of intentionally bad rows to exercise the transform
        if w == 17:
            a = None
        if w == 33:
            b = None
        rows.append({
            "week_start": (base + timedelta(weeks=w)).isoformat(),
            "revenue_line_a": None if a is None else round(a, 2),
            "revenue_line_b": None if b is None else round(b, 2),
        })
    with open(path, "w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=["week_start", "revenue_line_a", "revenue_line_b"])
        wr.writeheader()
        wr.writerows(rows)


# --------------------------------------------------------------------------
# SELFTEST (deterministic, offline, no plotting required)
# --------------------------------------------------------------------------

def selftest() -> int:
    tmp = tempfile.mkdtemp(prefix="seaborn_poc_")
    csv_path = os.path.join(tmp, "sample.csv")
    make_sample_csv(csv_path)
    raw = extract(csv_path)
    assert len(raw) == 52, f"expected 52 rows, got {len(raw)}"
    cd = transform(raw)
    assert cd.date_col == "week_start"
    assert set(cd.metric_cols) == {"revenue_line_a", "revenue_line_b"}, cd.metric_cols
    assert cd.rows == 52 and cd.dropped == 0, (cd.rows, cd.dropped)
    assert cd.frame[cd.metric_cols].notna().all().all(), "gaps were not filled"
    # ordering
    dates = pd.to_datetime(cd.frame[cd.date_col])
    assert dates.is_monotonic_increasing
    enr = enrich(cd)
    assert "rolling_7_revenue_line_a" in enr.columns
    assert "delta_revenue_line_a" in enr.columns
    assert math.isfinite(enr["rolling_7_revenue_line_a"].iloc[-1])
    # bad-row injection: corrupt a row, transform must drop it cleanly
    bad = raw.copy()
    bad.loc[5, "week_start"] = "not-a-date"
    cd2 = transform(bad)
    assert cd2.rows == 51 and cd2.dropped == 1, (cd2.rows, cd2.dropped)
    # detect_columns on a tricky frame
    tricky = pd.DataFrame({
        "id": range(10),
        "date": [f"2026-01-0{i+1}" if i < 9 else "2026-01-10" for i in range(10)],
        "value": [float(i) for i in range(10)],
    })
    dcol, num = detect_columns(tricky)
    assert dcol == "date" and num == ["value"], (dcol, num)
    print("SELFTEST: all checks passed")
    return 0


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="ETL + Seaborn dashboard export PoC")
    ap.add_argument("csv", nargs="?", default=None,
                    help="input CSV (default: generated sample)")
    ap.add_argument("--out", default=OUT_DIR, help="output dir")
    ap.add_argument("--title", default="Weekly revenue dashboard (sample data)")
    ap.add_argument("--selftest", action="store_true", help="run offline checks only")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if args.csv:
        src = args.csv
    else:
        src = os.path.join(HERE, "sample_data.csv")
        make_sample_csv(src)
        print(f"no CSV given - generated sample at {src}")

    raw = extract(src)
    cd = transform(raw)
    print(f"extracted {len(raw)} rows -> clean {cd.rows} rows "
          f"(dropped {cd.dropped}), date={cd.date_col}, metrics={cd.metric_cols}")

    if not HAVE_PLOTTING:
        print("matplotlib/seaborn not installed - skipping export; run --selftest to verify.")
        return 0

    paths = export_dashboard(cd, out_dir=args.out, title=args.title)
    for k, v in paths.items():
        size = os.path.getsize(v)
        print(f"  {k}: {v}  ({size:,} bytes)")
    print("DONE - dashboard exported.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
