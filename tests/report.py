#!/usr/bin/env python3
"""One `check()` for every checkpoint file. Not a test itself.

Borrowed from homegraph's `tests/report.py` (registered in the borrow ledger),
including the two things that are not obvious and were both paid for there:

  * **Not one shared list.** pytest imports every checkpoint file into one
    process. A module-level `results` here would mix their counts, and one red
    check in cp0 would fail every later `main()` in the same run. `reporter()`
    hands out a fresh list per caller.
  * **A red check also reports itself machine-readably.** `mutate.py` decides
    which gate said no by reading stdout. Parsing the formatted report line is
    wrong in three ways that all occur in practice -- names longer than the
    column width lose their padding and swallow the detail, details containing
    a double space move the split inside the detail, and names containing a
    double space split in the middle. No whitespace split can be right for all
    three. So the report line is for humans, and a red check *also* prints one
    tab-separated `FAILED\\t<name>` line that the driver reads verbatim.
"""
from __future__ import annotations


def reporter(width: int = 52):
    """Return `(results, check)` for one test module."""
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, ok, detail))
        print("%s  %-*s %s" % ("PASS" if ok else "FAIL", width, name, detail))
        if not ok:
            print("FAILED\t%s" % name)
        return ok

    return results, check
