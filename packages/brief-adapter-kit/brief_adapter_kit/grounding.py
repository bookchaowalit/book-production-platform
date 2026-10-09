"""Evidence checks that tie model-proposed values back to the source brief.

REQ-AI-07 forbids trusting a model's own confidence: every critical value a
model proposes must be visibly present in the brief. These helpers find the
dates, numbers, and currencies a brief actually states so the provider adapter
can drop any value without evidence and abstain with ``needs_input``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

_THAI_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")
_NUMBER = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])\d+(?:\.\d+)?")

_EN_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_TH_MONTHS = {
    name: index
    for index, names in enumerate(
        (
            ("มกราคม", "ม.ค."), ("กุมภาพันธ์", "ก.พ."), ("มีนาคม", "มี.ค."), ("เมษายน", "เม.ย."),
            ("พฤษภาคม", "พ.ค."), ("มิถุนายน", "มิ.ย."), ("กรกฎาคม", "ก.ค."), ("สิงหาคม", "ส.ค."),
            ("กันยายน", "ก.ย."), ("ตุลาคม", "ต.ค."), ("พฤศจิกายน", "พ.ย."), ("ธันวาคม", "ธ.ค."),
        ),
        start=1,
    )
    for name in names
}
_MONTH_NAMES = "|".join(sorted((re.escape(name) for name in (*_EN_MONTHS, *_TH_MONTHS)), key=len, reverse=True))
_DAY_MONTH_YEAR = re.compile(rf"(?<!\d)(\d{{1,2}})\s*({_MONTH_NAMES})\s*,?\s*(\d{{4}})(?!\d)", re.IGNORECASE)
_MONTH_DAY_YEAR = re.compile(rf"({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*(\d{{4}})(?!\d)", re.IGNORECASE)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUMERIC_DATE = re.compile(r"(?<!\d)(\d{1,2})[/-](\d{1,2})[/-](\d{4})(?!\d)")

_CURRENCY_CODES = frozenset(
    ("THB", "USD", "EUR", "GBP", "JPY", "SGD", "CNY", "HKD", "AUD", "CAD", "CHF", "KRW", "MYR", "VND", "INR", "IDR", "PHP")
)
_CURRENCY_ALIASES = {
    "บาท": "THB", "฿": "THB", "ดอลลาร์": "USD", "$": "USD", "ยูโร": "EUR", "€": "EUR",
    "ปอนด์": "GBP", "£": "GBP", "เยน": "JPY", "¥": "JPY",
}


# Eval canaries (e.g. ``CANARY-clear-en-01``) are privacy markers, never evidence.
_CANARY = re.compile(r"CANARY[-\w]*")


def _evidence_text(value: str) -> str:
    return _CANARY.sub(" ", unicodedata.normalize("NFC", value)).translate(_THAI_DIGITS)


def normalize_text(value: str) -> str:
    """Normalize for evidence comparison: NFC, Thai digits, casefold, collapsed whitespace."""

    text = unicodedata.normalize("NFC", value).translate(_THAI_DIGITS).casefold()
    return re.sub(r"\s+", " ", text).strip()


def text_grounded(value: str, brief: str) -> bool:
    """True when the normalized value appears verbatim in the normalized brief."""

    needle = normalize_text(value)
    return bool(needle) and needle in normalize_text(brief)


def brief_numbers(brief: str) -> set[Decimal]:
    numbers: set[Decimal] = set()
    for match in _NUMBER.findall(_evidence_text(brief)):
        try:
            numbers.add(Decimal(match.replace(",", "")).normalize())
        except InvalidOperation:
            continue
    return numbers


def number_grounded(value: str, brief: str) -> bool:
    try:
        return Decimal(value).normalize() in brief_numbers(brief)
    except InvalidOperation:
        return False


def _gregorian(year: int) -> int:
    # Thai documents commonly use the Buddhist Era (BE = CE + 543).
    return year - 543 if year >= 2400 else year


def _safe_date(year: int, month: int, day: int) -> str | None:
    try:
        return date(_gregorian(year), month, day).isoformat()
    except ValueError:
        return None


def brief_dates(brief: str) -> set[str]:
    """Return every ISO date the brief states explicitly."""

    text = _evidence_text(brief)
    found: set[str | None] = set()
    for year, month, day in _ISO_DATE.findall(text):
        found.add(_safe_date(int(year), int(month), int(day)))
    for day, month, year in _NUMERIC_DATE.findall(text):
        found.add(_safe_date(int(year), int(month), int(day)))
    for day, month_name, year in _DAY_MONTH_YEAR.findall(text):
        month = _EN_MONTHS.get(month_name.casefold()) or _TH_MONTHS.get(month_name)
        if month:
            found.add(_safe_date(int(year), month, int(day)))
    for month_name, day, year in _MONTH_DAY_YEAR.findall(text):
        month = _EN_MONTHS.get(month_name.casefold()) or _TH_MONTHS.get(month_name)
        if month:
            found.add(_safe_date(int(year), month, int(day)))
    return {value for value in found if value is not None}


def brief_currencies(brief: str) -> set[str]:
    """Return the distinct currency codes a brief names (by code, symbol, or Thai/English word)."""

    text = unicodedata.normalize("NFC", brief)
    codes = {code for code in re.findall(r"(?<![A-Za-z])[A-Za-z]{3}(?![A-Za-z])", text) if code.upper() == code and code in _CURRENCY_CODES}
    for alias, code in _CURRENCY_ALIASES.items():
        if alias in text:
            codes.add(code)
    if re.search(r"\b(?:dollars?|usd)\b", text, re.IGNORECASE):
        codes.add("USD")
    if re.search(r"\b(?:baht)\b", text, re.IGNORECASE):
        codes.add("THB")
    if re.search(r"\b(?:euros?)\b", text, re.IGNORECASE):
        codes.add("EUR")
    return codes


__all__ = [
    "brief_currencies",
    "brief_dates",
    "brief_numbers",
    "normalize_text",
    "number_grounded",
    "text_grounded",
]
