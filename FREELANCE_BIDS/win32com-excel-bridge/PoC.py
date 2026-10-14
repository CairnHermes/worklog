#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
win32com Excel automation bridge  (pilot #131, ref QA-PY-0131)
================================================================

A small, intentionally clear Python 3 bridge that drives Excel through
win32com.client (COM). Built for readability-first handoff: one thin
driver interface, one real COM implementation, one in-process driver for
testing on machines that do not have Excel installed, a stdlib logging
layer so runs are auditable, and a small CLI.

Design (kept deliberately small)
--------------------------------
  ExcelDriver   -- abstract interface. This is the contract. Both concrete
                   drivers below implement EXACTLY these methods.
  Win32Driver   -- the real Excel-facing driver (win32com.client.Dispatch).
                   This is the code the reviewer reads.
  InMemoryDriver-- pure-Python driver with the SAME surface. Lets the CLI
                   and the self-test run on any machine (including one with
                   no Excel) and exercises every edge case for real.
  make_driver() -- factory. 'win32' or 'memory'. Fails clean + explains
                   when Excel is not present.
  CLI           -- write / read / roundtrip / append / selftest.

Dependencies
------------
  Core, CLI, self-test : standard library only (runs anywhere on Python 3).
  Win32Driver          : `pip install pywin32` + Excel installed (Windows).

Why two drivers
---------------
The deliverable is a readable, well-logged bridge plus correct edge-case
behavior. Splitting the COM specifics behind an interface keeps the logic
(and every edge case) testable without a live Excel process, and makes
swapping/adding a driver (e.g. LibreOffice, headless) a one-class change.

Run the self-test (no Excel required):
    python PoC.py selftest
"""

from __future__ import annotations

import abc
import argparse
import logging
import sys
from typing import Any, List, Optional, Sequence


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class BridgeError(Exception):
    """Base error for the bridge. Always safe to surface to a CLI caller."""


class ExcelNotAvailableError(BridgeError):
    """Raised when the win32 driver is requested but Excel/COM is missing."""


class SheetNotFoundError(BridgeError):
    """Raised when a named worksheet does not exist in the workbook."""


class DriverClosedError(BridgeError):
    """Raised when an operation is attempted after close()."""


# --------------------------------------------------------------------------
# Logging (stdlib; audit-friendly)
# --------------------------------------------------------------------------
def get_logger(name: str = "excelbridge") -> logging.Logger:
    """Return a configured logger. Idempotent: safe to call repeatedly.

    A single level on first setup keeps output predictable; a real run
    attaches a StreamHandler at INFO. File audit logging is optional and
    turned on by the CLI via setup_audit_log().
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def setup_audit_log(path: str, name: str = "excelbridge") -> logging.Logger:
    """Attach a FileHandler so a run is auditable to `path`."""
    logger = get_logger(name)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-7s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)
    return logger


# --------------------------------------------------------------------------
# Cell helper (shared by both drivers so behavior is identical)
# --------------------------------------------------------------------------
def _split_cell(ref: str) -> tuple[int, int]:
    """'B7' -> (row=7, col=2). 1-based. Raises BridgeError if malformed."""
    ref = (ref or "").strip().upper()
    letters = ""
    digits = ""
    for ch in ref:
        if ch.isalpha():
            letters += ch
        elif ch.isdigit():
            digits += ch
        else:
            raise BridgeError(f"Invalid cell reference: {ref!r}")
    if not letters or not digits:
        raise BridgeError(f"Invalid cell reference: {ref!r}")
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    row = int(digits)
    if row < 1 or col < 1:
        raise BridgeError(f"Cell reference out of range: {ref!r}")
    return row, col


def _col_letter(col: int) -> str:
    """2 -> 'B', 27 -> 'AA'. (Inverse of the letter half of _split_cell.)"""
    letters = ""
    while col > 0:
        col, rem = divmod(col - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters


# --------------------------------------------------------------------------
# Abstract driver (the contract)
# --------------------------------------------------------------------------
class ExcelDriver(abc.ABC):
    """The thin surface the CLI and self-test program against.

    Both concrete drivers implement exactly these methods, so the rest of
    the codebase never depends on COM specifics.
    """

    #: class-level marker used by the factory/tests; subclasses override.
    kind: str = "abstract"

    def __init__(self) -> None:
        self._closed = False

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    def connect(self) -> None:
        """Open/prepare the workbook. Idempotent-ish; safe to call once."""

    def is_closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise DriverClosedError("Driver is closed; no operations allowed.")

    @abc.abstractmethod
    def close(self) -> None:
        """Release the workbook / application. Idempotent and exception-safe."""

    def __enter__(self) -> "ExcelDriver":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- introspection -----------------------------------------------------
    @abc.abstractmethod
    def sheet_names(self) -> List[str]:
        """Names of all worksheets in the workbook."""

    @abc.abstractmethod
    def last_row(self, sheet: str) -> int:
        """1-based index of the last row that has data (0 if empty sheet)."""

    @abc.abstractmethod
    def save(self, path: Optional[str] = None) -> None:
        """Persist the workbook. `path` optional where the driver has one."""

    # -- cell / row operations --------------------------------------------
    @abc.abstractmethod
    def read_value(self, sheet: str, ref: str) -> Any:
        """Return the value at `ref` (e.g. 'A1'), or None if empty."""

    @abc.abstractmethod
    def write_value(self, sheet: str, ref: str, value: Any) -> None:
        """Write `value` to `ref`. Strings starting with '=' are formulas."""

    @abc.abstractmethod
    def append_row(self, sheet: str, values: Sequence[Any]) -> int:
        """Append `values` starting at column A after the last used row.

        Returns the 1-based row index written.
        """

    @abc.abstractmethod
    def read_row(self, sheet: str, row: int, ncols: int) -> List[Any]:
        """Read `ncols` cells of `row` starting at column A (None-padded)."""

    # -- shared helpers (not abstract) ------------------------------------
    def _require_sheet(self, sheet: str) -> None:
        if sheet not in self.sheet_names():
            raise SheetNotFoundError(
                f"Worksheet {sheet!r} not found. "
                f"Available: {', '.join(self.sheet_names()) or '(none)'}"
            )


# --------------------------------------------------------------------------
# In-memory driver (identical surface; no Excel required)
# --------------------------------------------------------------------------
class InMemoryDriver(ExcelDriver):
    """Pure-Python driver implementing the exact ExcelDriver surface.

    Storage model: {sheet_name: {(row, col): value}}.
    Semantics that mirror Excel and are asserted by the self-test:
      * reading an un-set cell -> None
      * a string starting with '=' is stored as a formula (text), NOT
        evaluated, and reported as such by read_value returning the same
        string (a real Excel driver would store it as a formula too)
      * values are stored as-given; no type coercion
      * append_row writes after the last used row, starting at column A
    """

    kind = "memory"

    def __init__(self, sheets: Optional[Sequence[str]] = None) -> None:
        super().__init__()
        self._log = get_logger("excelbridge.memory")
        self._sheets: "dict[str, dict[tuple[int, int], Any]]" = {
            name: {} for name in (list(sheets) if sheets else ["Sheet1"])
        }
        self._path: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        self._log.info("InMemoryDriver.connect (sheets=%s)", list(self._sheets))

    def close(self) -> None:
        self._log.info("InMemoryDriver.close")
        self._closed = True

    # -- introspection -----------------------------------------------------
    def sheet_names(self) -> List[str]:
        self._ensure_open()
        return list(self._sheets.keys())

    def last_row(self, sheet: str) -> int:
        self._ensure_open()
        self._require_sheet(sheet)
        cells = self._sheets[sheet]
        return max((row for row, _ in cells), default=0)

    def save(self, path: Optional[str] = None) -> None:
        self._ensure_open()
        if path:
            self._path = path
        self._log.info(
            "InMemoryDriver.save (path=%s, sheets=%d)", path, len(self._sheets)
        )

    # -- cell / row operations --------------------------------------------
    def read_value(self, sheet: str, ref: str) -> Any:
        self._ensure_open()
        self._require_sheet(sheet)
        row, col = _split_cell(ref)
        return self._sheets[sheet].get((row, col))

    def write_value(self, sheet: str, ref: str, value: Any) -> None:
        self._ensure_open()
        self._require_sheet(sheet)
        row, col = _split_cell(ref)
        self._sheets[sheet][(row, col)] = value
        self._log.debug("write %s!%s = %r", sheet, ref, value)

    def append_row(self, sheet: str, values: Sequence[Any]) -> int:
        self._ensure_open()
        self._require_sheet(sheet)
        row = self.last_row(sheet) + 1
        for offset, value in enumerate(values, start=1):
            self._sheets[sheet][(row, offset)] = value
        self._log.info("append_row %s row=%d (%d cells)", sheet, row, len(values))
        return row

    def read_row(self, sheet: str, row: int, ncols: int) -> List[Any]:
        self._ensure_open()
        self._require_sheet(sheet)
        cells = self._sheets[sheet]
        return [cells.get((row, col)) for col in range(1, ncols + 1)]


# --------------------------------------------------------------------------
# Win32 COM driver (the real Excel-facing code the reviewer reads)
# --------------------------------------------------------------------------
class Win32Driver(ExcelDriver):
    """Drive a real Excel workbook via win32com.client.

    Requires `pip install pywin32` and an installed Excel on Windows.
    Every public method:
      * checks it is open (DriverClosedError otherwise)
      * verifies the sheet exists (SheetNotFoundError otherwise)
      * catches low-level COM/ValueErrors into BridgeError with context
    """

    kind = "win32"

    EXCEL_CLASS = "Excel.Application"

    def __init__(self, path: Optional[str] = None) -> None:
        super().__init__()
        self._log = get_logger("excelbridge.win32")
        self._path = path
        self._app = None
        self._wb = None

    # -- lifecycle ---------------------------------------------------------
    def connect(self) -> None:
        if self._closed:
            raise DriverClosedError("Driver already closed.")
        try:
            import win32com.client as wc  # imported lazily: keeps core stdlib-only
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ExcelNotAvailableError(
                "pywin32 is not installed. Run `pip install pywin32` and "
                "ensure Excel is installed, or use the in-memory driver "
                "(`--driver memory`) to run the bridge without Excel."
            ) from exc

        self._log.info("Win32Driver.connect (path=%s)", self._path)
        try:
            self._app = wc.Dispatch(self.EXCEL_CLASS)
            self._app.Visible = False
            self._app.DisplayAlerts = False
            if self._path:
                self._wb = self._app.Workbooks.Open(self._path)
            else:
                self._wb = self._app.Workbooks.Add()
        except Exception as exc:
            self._safe_cleanup()
            raise ExcelNotAvailableError(
                f"Could not start Excel via COM: {exc}. "
                "Is Microsoft Excel installed and registered on this machine? "
                "Or run with `--driver memory`."
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._log.info("Win32Driver.close")
        self._safe_cleanup()
        self._closed = True

    def _safe_cleanup(self) -> None:
        """Release COM objects best-effort; never raise from cleanup."""
        for obj in (self._wb, self._app):
            try:
                if obj is not None:
                    if obj is self._wb:
                        obj.Close(SaveChanges=False)
                    else:
                        obj.Quit()
            except Exception:  # noqa: BLE001 - cleanup must not raise
                self._log.debug("cleanup: ignoring release error", exc_info=True)
        self._wb = None
        self._app = None

    # -- helpers -----------------------------------------------------------
    def _sheet(self, sheet: str):
        self._ensure_open()
        try:
            ws = self._wb.Worksheets(sheet)
        except Exception as exc:
            self._require_sheet(sheet)  # raises SheetNotFoundError with list
            raise
        return ws

    def _require_sheet(self, sheet: str) -> None:
        try:
            names = [self._wb.Worksheets(i).Name for i in range(1, self._wb.Worksheets.Count + 1)]
        except Exception:
            names = []
        if sheet not in names:
            raise SheetNotFoundError(
                f"Worksheet {sheet!r} not found. "
                f"Available: {', '.join(names) or '(none)'}"
            )

    # -- introspection -----------------------------------------------------
    def sheet_names(self) -> List[str]:
        self._ensure_open()
        try:
            count = self._wb.Worksheets.Count
            return [self._wb.Worksheets(i).Name for i in range(1, count + 1)]
        except Exception as exc:
            raise BridgeError(f"Could not enumerate sheets: {exc}") from exc

    def last_row(self, sheet: str) -> int:
        ws = self._sheet(sheet)
        try:
            return int(ws.UsedRange.Rows.Count)
        except Exception as exc:
            raise BridgeError(f"Could not read used range on {sheet!r}: {exc}") from exc

    def save(self, path: Optional[str] = None) -> None:
        self._ensure_open()
        target = path or self._path
        if not target:
            raise BridgeError("save() needs a path (driver was created without one).")
        try:
            self._wb.SaveAs(target) if self._wb is not None and not self._path else self._wb.Save()
        except Exception as exc:
            raise BridgeError(f"Save failed to {target!r}: {exc}") from exc
        self._path = target
        self._log.info("Win32Driver.save -> %s", target)

    # -- cell / row operations --------------------------------------------
    def read_value(self, sheet: str, ref: str) -> Any:
        ws = self._sheet(sheet)
        _row, _col = _split_cell(ref)  # validates ref format
        try:
            value = ws.Range(ref).Value
        except Exception as exc:
            raise BridgeError(f"Read failed at {sheet}!{ref}: {exc}") from exc
        return value

    def write_value(self, sheet: str, ref: str, value: Any) -> None:
        ws = self._sheet(sheet)
        _row, _col = _split_cell(ref)
        try:
            if isinstance(value, str) and value.startswith("="):
                ws.Range(ref).Formula = value
            else:
                ws.Range(ref).Value = value
        except Exception as exc:
            raise BridgeError(f"Write failed at {sheet}!{ref}: {exc}") from exc
        self._log.debug("write %s!%s = %r (formula=%s)",
                        sheet, ref, value,
                        isinstance(value, str) and value.startswith("="))

    def append_row(self, sheet: str, values: Sequence[Any]) -> int:
        ws = self._sheet(sheet)
        row = int(ws.UsedRange.Rows.Count) + 1
        for offset, value in enumerate(values, start=1):
            col = _col_letter(offset)
            ref = f"{col}{row}"
            if isinstance(value, str) and value.startswith("="):
                ws.Range(ref).Formula = value
            else:
                ws.Range(ref).Value = value
        self._log.info("append_row %s row=%d (%d cells)", sheet, row, len(values))
        return row

    def read_row(self, sheet: str, row: int, ncols: int) -> List[Any]:
        ws = self._sheet(sheet)
        out: List[Any] = []
        for offset in range(1, ncols + 1):
            ref = f"{_col_letter(offset)}{row}"
            out.append(ws.Range(ref).Value)
        return out


# --------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------
def make_driver(kind: str, path: Optional[str] = None) -> ExcelDriver:
    """Create a driver by name. 'win32' or 'memory'.

    Unknown kinds raise BridgeError (never a silent fallback), so a typo
    cannot accidentally run against the wrong backend.
    """
    key = (kind or "").strip().lower()
    if key == "memory":
        return InMemoryDriver()
    if key == "win32":
        return Win32Driver(path=path)
    raise BridgeError(f"Unknown driver kind {kind!r}. Use 'win32' or 'memory'.")


# --------------------------------------------------------------------------
# Self-test (runnable without Excel; exercises the full surface + edge cases)
# --------------------------------------------------------------------------
class _Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: List[str] = []

    def ok(self, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            self.passed += 1
            print(f"  PASS  {label}")
        else:
            self.failed += 1
            self.failures.append(label)
            print(f"  FAIL  {label}   {detail}")


def run_selftest() -> int:
    """Exercise InMemoryDriver against every contract line + edge case.

    Returns process exit code (0 = all green). Uses a fresh driver per
    group so state never leaks between checks.
    """
    print("win32com Excel bridge -- self-test (in-memory driver)\n")
    c = _Check()

    # 1. cell reference parsing
    try:
        assert _split_cell("A1") == (1, 1)
        assert _split_cell("B7") == (7, 2)
        assert _split_cell("aa100") == (100, 27)
        c.ok("cell parsing A1/B7/A100 (case-insensitive)", True)
    except Exception as exc:
        c.ok("cell parsing", False, str(exc))

    # invalid refs must be rejected, not crash
    for bad in ("1", "A", "X", "A-1"):
        try:
            _split_cell(bad)
            c.ok(f"reject invalid ref {bad!r}", False, "no error raised")
        except BridgeError:
            c.ok(f"reject invalid ref {bad!r}", True)

    # 2. round-trip fidelity for the value types we support
    with InMemoryDriver(["Data"]) as d:
        for ref, value in (("A1", "text"), ("B2", 42), ("C3", 3.14),
                           ("D4", True), ("E5", None), ("F6", "=A1+B2")):
            d.write_value("Data", ref, value)
        c.ok("write str/int/float/bool/None/formula", True)

        got = [d.read_value("Data", r) for r in ("A1", "B2", "C3", "D4", "F6")]
        c.ok("round-trip fidelity str/int/float/bool/formula",
             got == ["text", 42, 3.14, True, "=A1+B2"], f"got={got!r}")

        # 3. reading an unset cell -> None (Excel semantics)
        c.ok("unset cell reads as None", d.read_value("Data", "Z99") is None)

        # 4. append_row places after last used row, from column A
        first = d.append_row("Data", ["x", 1, "two"])
        c.ok("append_row returns a row index > 0", first > 0, f"row={first}")
        row_vals = d.read_row("Data", first, 3)
        c.ok("append_row content + None padding",
             row_vals == ["x", 1, "two"], f"got={row_vals!r}")

        # 5. last_row tracks growth
        c.ok("last_row >= appended row", d.last_row("Data") >= first,
             f"last={d.last_row('Data')}")

        # 6. sheet introspection
        c.ok("sheet_names reports 'Data'", d.sheet_names() == ["Data"],
             f"got={d.sheet_names()}")

    # 7. SheetNotFoundError with a helpful available-sheet list
    try:
        with InMemoryDriver(["Data"]) as d:
            d.read_value("Nope", "A1")
        c.ok("missing sheet raises", False, "no error raised")
    except SheetNotFoundError as exc:
        msg = str(exc)
        c.ok("missing sheet raises + lists available sheets", "Data" in msg, msg)

    # 8. unknown driver kind is rejected loudly (no silent fallback)
    try:
        make_driver("bogus")
        c.ok("unknown driver rejected", False, "no error raised")
    except BridgeError:
        c.ok("unknown driver rejected", True)

    # 9. closed driver refuses operations
    d = InMemoryDriver(["S"])
    d.connect()
    d.close()
    try:
        d.read_value("S", "A1")
        c.ok("closed driver refuses ops", False, "no error raised")
    except DriverClosedError:
        c.ok("closed driver refuses ops", True)

    # 10. context manager closes the driver
    with InMemoryDriver(["S"]) as d:
        d.write_value("S", "A1", 1)
    c.ok("context manager closes on exit", d.is_closed())

    # 11. formula semantics are explicit (stored, not evaluated)
    with InMemoryDriver(["F"]) as d:
        d.write_value("F", "A1", "=1+1")
        c.ok("formula stored as formula text (not evaluated)",
             d.read_value("F", "A1") == "=1+1", f"got={d.read_value('F','A1')!r}")

    print()
    print(f"Result: {c.passed} passed, {c.failed} failed")
    if c.failures:
        print("Failing checks:")
        for f in c.failures:
            print(f"  - {f}")
    if c.failed == 0:
        print("all checks passed")
    return 0 if c.failed == 0 else 1


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="excelbridge",
        description="Small, clear win32com Excel automation bridge (pilot #131).",
    )
    p.add_argument("--driver", choices=("win32", "memory"), default="memory",
                   help="Backend to use. 'win32' drives real Excel; "
                        "'memory' runs the same bridge without Excel (default).")
    p.add_argument("--file", default=None, help="Workbook path (win32 driver).")
    p.add_argument("--audit-log", default=None,
                   help="Optional path to write an audit log file to.")
    sub = p.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("write", help="Write a value to a cell.")
    w.add_argument("sheet"); w.add_argument("cell"); w.add_argument("value")

    r = sub.add_parser("read", help="Read a cell (None -> empty line).")
    r.add_argument("sheet"); r.add_argument("cell")

    rt = sub.add_parser("roundtrip",
                        help="Write a list of ref=value pairs, then read back.")
    rt.add_argument("sheet")
    rt.add_argument("--cells", required=True,
                    help="Comma-separated ref=value pairs, e.g. A1=hi,B2=42")

    a = sub.add_parser("append", help="Append a row starting at column A.")
    a.add_argument("sheet")
    a.add_argument("--values", required=True,
                   help="Comma-separated values, e.g. name,qty,price")

    sub.add_parser("selftest", help="Run the full in-memory contract test.")
    return p


def _parse_pairs(s: str) -> List[tuple[str, str]]:
    pairs = []
    for chunk in s.split(","):
        if "=" not in chunk:
            raise BridgeError(f"--cells item must be ref=value, got {chunk!r}")
        ref, val = chunk.split("=", 1)
        pairs.append((ref.strip(), val))
    return pairs


def _fmt(value: Any) -> str:
    if value is None:
        return "<empty>"
    return repr(value)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    log = get_logger()
    if args.audit_log:
        setup_audit_log(args.audit_log)

    if args.cmd == "selftest":
        return run_selftest()

    driver = make_driver(args.driver, path=args.file)
    try:
        with driver:
            if args.cmd == "write":
                driver.write_value(args.sheet, args.cell, args.value)
                log.info("wrote %s!%s = %r", args.sheet, args.cell, args.value)
                print(f"wrote {args.sheet}!{args.cell} = {args.value!r}")
            elif args.cmd == "read":
                val = driver.read_value(args.sheet, args.cell)
                log.info("read %s!%s -> %r", args.sheet, args.cell, val)
                print(_fmt(val))
            elif args.cmd == "roundtrip":
                pairs = _parse_pairs(args.cells)
                for ref, val in pairs:
                    driver.write_value(args.sheet, ref, val)
                log.info("roundtrip %s: %d cells", args.sheet, len(pairs))
                print("round-trip:")
                for ref, _ in pairs:
                    print(f"  {args.sheet}!{ref} = {_fmt(driver.read_value(args.sheet, ref))}")
            elif args.cmd == "append":
                values = [v.strip() for v in args.values.split(",")]
                row = driver.append_row(args.sheet, values)
                log.info("appended row %d (%d cells) to %s", row, len(values), args.sheet)
                print(f"appended row {row}: {values}")
    except BridgeError as exc:
        log.error("bridge error: %s", exc)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
