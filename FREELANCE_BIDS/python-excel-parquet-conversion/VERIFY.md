# VERIFY - Excel to Parquet converter

Verified on this host: Python 3.13.15 (hermes-env), pandas 3.0.5, pyarrow
25.0.1, openpyxl 3.1.5. No network access required for any check.

## Self-test (deterministic)

Command: `python PoC.py selftest`

Result: **all 17 checks passed, exit 0.**

Checks (per-case, each PASS/FAIL reported):
1. all 3 sheets converted
2. total_rows == 6
3. orders row count == 3
4. orders order_id values (ints preserved)
5. orders qty int preserved
6. orders unit_price floats preserved
7. orders discount floats preserved
8. orders note strings (incl. null) preserved
9. orders placed dates preserved (real Parquet timestamps, read back as dates)
10. flags active booleans preserved
11. flags empty_col all null (stable schema, not a null blob)
12. flags id ints preserved
13. empty sheet row count == 0
14. empty sheet schema has 3 cols (header-only sheet keeps a stable schema)
15. single-sheet conversion -> 1 file
16. missing file raises FileNotFoundError
17. non-xlsx path raises ValueError

## Live convert (real workbook -> Parquet, read back with pyarrow)

Built a multi-sheet workbook with openpyxl (typed ints, floats, booleans,
datetime cells, a fully-empty column, and a header-only sheet), ran
`convert_excel_to_parquet`, and read the output back with
`pyarrow.parquet.read_table`. Observed Parquet schema for the `orders` sheet:

```
order_id: int64
qty: int64
unit_price: double
discount: double
placed: timestamp[us]
note: large_string
```

All values round-tripped exactly (ints, decimals, booleans, dates, strings,
and nulls). This is the real engine path the client would run (pandas reads
the .xlsx via openpyxl, pyarrow writes the Parquet), not a mock.

## Honest limits

- Covers modern .xlsx / .xlsm. Legacy .xls binary is out of scope (would need
  xlrd) and is raised as a question in the bid, not assumed.
- Multi-sheet output naming is `<stem>__<sheet>.parquet`; the bid asks the
  client for any required convention before it is locked in.
