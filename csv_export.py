"""CSV export helpers for spreadsheet-safe user-provided text."""

import unicodedata

import pandas as pd


_FORMULA_PREFIXES = {"=", "+", "-", "@"}


def _spreadsheet_safe_value(value):
    if not isinstance(value, str):
        return value

    for character in value:
        if character in _FORMULA_PREFIXES:
            return "'" + value
        if unicodedata.category(character) not in {"Cc", "Cf", "Zs", "Zl", "Zp"}:
            break
    return value


def spreadsheet_safe_csv(frame: pd.DataFrame) -> bytes:
    """Return UTF-8-BOM CSV with formula-like text cells forced to text."""
    safe_frame = frame.map(_spreadsheet_safe_value)
    return safe_frame.to_csv(index=False).encode("utf-8-sig")
