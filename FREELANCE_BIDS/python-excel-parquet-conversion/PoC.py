#!/usr/bin/env python3
"""Excel -> Parquet conversion utility.

Idiomatic, minimal-dependency converter that reads ``.xlsx`` workbooks and
writes each sheet to an Apache Parquet file with well-typed columns (ints,
floats, booleans, timestamps and strings). Deterministic and fully
self-testing with no network access.

Public surface
--------------
- :func:`convert_excel_to_parquet`
- :func:`read_workbook_sheets`
- :class:`ConversionResult`

CLI
---
    python PoC.py selftest
    python PoC.py convert INPUT.xlsx [--out-dir DIR] [--sheet NAME]
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from typing import Dict, List, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ConversionResult:
    """Outcome of a single workbook conversion.

    Attributes
    ----------
    source:
        The input ``.xlsx`` path that was converted.
    out_dir:
        Directory the Parquet files were written to.
    sheets:
        Ordered mapping of sheet name -> written Parquet path.
    total_rows:
        Sum of data rows across all converted sheets.
    """

    source: str
    out_dir: str
    sheets: Dict[str, str]
    total_rows: int

    def summary(self) -> str:
        """Human-readable one-line-per-sheet summary string."""
        lines = [f"{name}: {path}"]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_workbook_sheets(
    xlsx_path: str,
    sheet_name: Optional[str] = None,
) -> Dict[str, "pd.DataFrame"]:
    """Read one or more sheets from an ``.xlsx`` file into DataFrames.

    Parameters
    ----------
    xlsx_path:
        Path to the ``.xlsx`` workbook.
    sheet_name:
        Optional single sheet to read. ``None`` reads every sheet.

    Returns
    -------
    Dict[str, pd.DataFrame]
        Mapping of sheet name -> DataFrame, in workbook order.

    Raises
    ------
    FileNotFoundError
        If ``xlsx_path`` does not exist.
    ValueError
        If ``sheet_name`` is given but is not present in the workbook.
    """
    if not os.path.isfile(xlsx_path):
        raise FileNotFoundError(f"input workbook not found: {xlsx_path}")
    if not xlsx_path.lower().endswith((".xlsx", ".xlsm")):
        raise ValueError(
            "expected an .xlsx/.xlsm workbook, got: "
            f"{os.path.basename(xlsx_path)}"
        )

    if sheet_name is not None:
        return {sheet_name: pd.read_excel(xlsx_path, sheet_name=sheet_name)}

    # sheet_name=None -> ordered dict of every sheet (Excel-compatible only;
    # .xls legacy binary is out of scope and raises a clear error).
    frames: Dict[str, pd.DataFrame] = pd.read_excel(
        xlsx_path, sheet_name=None
    )
    if not frames:
        raise ValueError(f"no sheets found in {xlsx_path}")
    return frames


def convert_excel_to_parquet(
    xlsx_path: str,
    out_dir: str,
    sheet_name: Optional[str] = None,
    compression: str = "snappy",
) -> ConversionResult:
    """Convert an ``.xlsx`` workbook (one or all sheets) to Parquet.

    Each sheet becomes ``<out_dir>/<sanitized-sheet-name>.parquet``.
    Column dtypes are inferred by pandas on read and stored in their natural
    Parquet physical type (int64 / float64 / bool / timestamp / string).

    Parameters
    ----------
    xlsx_path:
        Path to the input ``.xlsx`` workbook.
    out_dir:
        Directory to write Parquet files into (created if missing).
    sheet_name:
        Optional single sheet to convert. ``None`` converts every sheet.
    compression:
        Parquet compression codec; default ``"snappy"``.

    Returns
    -------
    ConversionResult
        Describes what was written and the total row count.
    """
    frames = read_workbook_sheets(xlsx_path, sheet_name=sheet_name)

    os.makedirs(out_dir, exist_ok=True)

    base = os.path.basename(xlsx_path)
    stem = os.path.splitext(base)[0] or "workbook"

    sheets: Dict[str, str] = {}
    total_rows = 0
    for name, df in frames.items():
        # Sanitize the sheet name for use as a file name (no OS path chars).
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()
        safe = safe or "sheet"
        # When converting a single sheet keep the stem prefix minimal; for
        # multi-sheet use stem__sheet to avoid name collisions.
        fname = f"{stem}__{safe}.parquet" if len(frames) > 1 else f"{stem}.parquet"
        out_path = os.path.join(out_dir, fname)

        # Force a clean, parquet-friendly dtype layout before writing:
        #  - datetime64 stays datetime64 -> Parquet timestamp
        #  - bool, int64, float64 pass through natively
        #  - object columns holding strings -> large_string
        df = _coerce_for_parquet(df)
        df.to_parquet(out_path, engine="pyarrow", compression=compression, index=False)

        sheets[name] = out_path
        total_rows += int(len(df))

    return ConversionResult(
        source=os.path.abspath(xlsx_path),
        out_dir=os.path.abspath(out_dir),
        sheets=sheets,
        total_rows=total_rows,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _coerce_for_parquet(df: "pd.DataFrame") -> "pd.DataFrame":
    """Normalize a DataFrame to a parquet-friendly dtype layout.

    Empty object columns become string-typed so the Parquet schema is stable
    instead of an opaque ``null`` type.
    """
    out = df.copy()
    for col in out.columns:
        s = out[col]
        if s.dtype == object and s.notna().any():
            out[col] = s.astype("string")
    return out


# ---------------------------------------------------------------------------
# Self-test (deterministic, no network)
# ---------------------------------------------------------------------------


def _build_fixture_xlsx(path: str) -> None:
    """Write a multi-sheet fixture workbook covering the common dtypes."""
    import openpyxl

    wb = openpyxl.Workbook()

    # Sheet 1: mixed typed data
    ws = wb.active
    ws.title = "orders"
    ws.append(["order_id", "qty", "unit_price", "discount", "placed", "note"])
    rows = [
        [101, 3, 19.99, 0.10, "2026-01-04", "rush"],
        [102, 1, 5.50, 0.00, "2026-01-05", None],
        [103, 12, 2.25, 0.25, "2026-01-06", "bulk"],
    ]
    for r in rows:
        ws.append(r)
    # Force a real date cell (openpyxl keeps strings; set a datetime)
    import datetime

    ws["E2"] = datetime.datetime(2026, 1, 4)
    ws["E3"] = datetime.datetime(2026, 1, 5)
    ws["E4"] = datetime.datetime(2026, 1, 6)

    # Sheet 2: booleans + a fully-empty column
    ws2 = wb.create_sheet("flags")
    ws2.append(["id", "active", "empty_col"])
    ws2.append([1, True, None])
    ws2.append([2, False, None])
    ws2.append([3, True, None])

    # Sheet 3: an empty sheet (header only)
    ws3 = wb.create_sheet("empty")
    ws3.append(["a", "b", "c"])

    wb.save(path)


def selftest() -> int:
    """Run the full self-test suite. Returns 0 on success, 1 on failure."""
    import tempfile

    import pyarrow.parquet as pq

    checks: List[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        state = "PASS" if cond else "FAIL"
        checks.append(f"[{state}] {label}")
        if not cond:
            if detail:
                print(f"   {detail}")
        if not cond:
            raise AssertionError(label)

    with tempfile.TemporaryDirectory(prefix="xlsx2pq_selftest_") as tmp:
        fixture = os.path.join(tmp, "fixture.xlsx")
        _build_fixture_xlsx(fixture)
        out_dir = os.path.join(tmp, "out")

        res = convert_excel_to_parquet(fixture, out_dir)

        # 1. All three sheets produced a file.
        check("all 3 sheets converted", len(res.sheets) == 3, str(res.sheets))

        # 2. Row totals (orders 3 + flags 3 + empty 0 = 6).
        check("total_rows == 6", res.total_rows == 6, f"got {res.total_rows}")

        # 3. orders: values round-trip.
        orders_path = res.sheets["orders"]
        tbl = pq.read_table(orders_path)
        check("orders row count == 3", tbl.num_rows == 3, f"got {tbl.num_rows}")
        orders = tbl.to_pydict()
        check(
            "orders order_id values",
            orders["order_id"] == [101, 102, 103],
            str(orders.get("order_id")),
        )
        check(
            "orders qty int preserved",
            orders["qty"] == [3, 1, 12],
            str(orders.get("qty")),
        )
        check(
            "orders unit_price floats preserved",
            orders["unit_price"] == [19.99, 5.50, 2.25],
            str(orders.get("unit_price")),
        )
        check(
            "orders discount floats preserved",
            orders["discount"] == [0.10, 0.00, 0.25],
            str(orders.get("discount")),
        )
        check(
            "orders note strings (incl. null) preserved",
            orders["note"] == ["rush", None, "bulk"],
            str(orders.get("note")),
        )
        import datetime

        expected_dates = [
            datetime.datetime(2026, 1, 4),
            datetime.datetime(2026, 1, 5),
            datetime.datetime(2026, 1, 6),
        ]
        check(
            "orders placed dates preserved",
            [v.date() if hasattr(v, "date") else v for v in orders["placed"]]
            == [d.date() for d in expected_dates],
            str(orders.get("placed")),
        )

        # 4. flags: booleans preserved, empty col present.
        flags_path = res.sheets["flags"]
        flags = pq.read_table(flags_path).to_pydict()
        check("flags active booleans preserved", flags["active"] == [True, False, True],
              str(flags.get("active")))
        check("flags empty_col all null", flags["empty_col"] == [None, None, None],
              str(flags.get("empty_col")))
        check("flags id ints preserved", flags["id"] == [1, 2, 3],
              str(flags.get("id")))

        # 5. empty sheet: 0 rows, schema preserved.
        empty_path = res.sheets["empty"]
        etbl = pq.read_table(empty_path)
        check("empty sheet row count == 0", etbl.num_rows == 0, f"got {etbl.num_rows}")
        check(
            "empty sheet schema has 3 cols",
            etbl.num_columns == 3,
            str(etbl.schema.names),
        )

        # 6. single-sheet conversion path.
        single_out = os.path.join(tmp, "single")
        res_single = convert_excel_to_parquet(fixture, single_out, sheet_name="orders")
        check(
            "single-sheet conversion -> 1 file",
            len(res_single.sheets) == 1 and res_single.sheets["orders"].endswith("fixture.parquet"),
            str(res_single.sheets),
        )

        # 7. error: missing file.
        try:
            read_workbook_sheets(os.path.join(tmp, "nope.xlsx"))
            check("missing file raises FileNotFoundError", False)
        except FileNotFoundError:
            check("missing file raises FileNotFoundError", True)

        # 8. error: wrong extension.
        bad = os.path.join(tmp, "notexcel.csv")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("a,b\n1,2\n")
        try:
            read_workbook_sheets(bad)
            check("non-xlsx path raises ValueError", False)
        except ValueError:
            check("non-xlsx path raises ValueError", True)

    for line in checks:
        print(line)
    print(f"\nall {len(checks)} checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert Excel (.xlsx) workbooks to Apache Parquet."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_self = sub.add_parser("selftest", help="Run the deterministic self-test suite.")

    p_conv = sub.add_parser("convert", help="Convert an .xlsx file to Parquet.")
    p_conv.add_argument("input", help="Path to the input .xlsx workbook.")
    p_conv.add_argument("--out-dir", default="parquet_out", help="Output directory.")
    p_conv.add_argument("--sheet", default=None, help="Single sheet name (default: all).")
    p_conv.add_argument("--compression", default="snappy", help="Parquet codec.")

    args = parser.parse_args(argv)

    if args.cmd == "selftest":
        return selftest()

    if args.cmd == "convert":
        res = convert_excel_to_parquet(
            args.input, args.out_dir, sheet_name=args.sheet,
            compression=args.compression,
        )
        print(f"converted {res.source}")
        for name, path in res.sheets.items():
            print(f"  {name} -> {path}")
        print(f"total data rows: {res.total_rows}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(_main())
