"""Pure functions that turn one messy value into one clean value.

Every function returns the clean value, or None when the input is invalid.
No database, no files: easy to unit-test.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- headers

_SEP_RUN = re.compile(r"[_\s]+")


def normalize_header(header: str) -> str:
    """Lower-case, trim, collapse every run of '_' or whitespace to one space.

    We trim once more at the end so that a stray leading/trailing underscore
    ("phone_") still matches ("phone").
    """
    return _SEP_RUN.sub(" ", header.lower().strip()).strip()


def build_header_map(headers: list[str], fields: list[dict]) -> dict[str, int]:
    """Map schema field name -> column index in the CSV.

    A column matches a field if its normalised header equals the field's
    normalised name or any normalised alias. Unmatched columns are ignored.
    If two columns match the same field, the first (left-most) one wins.
    """
    lookup: dict[str, str] = {}
    for field in fields:
        for candidate in [field["name"], *field.get("aliases", [])]:
            lookup.setdefault(normalize_header(candidate), field["name"])

    mapping: dict[str, int] = {}
    for index, header in enumerate(headers):
        field_name = lookup.get(normalize_header(header))
        if field_name is not None and field_name not in mapping:
            mapping[field_name] = index
    return mapping


# ---------------------------------------------------------------- phone

_NON_DIGIT = re.compile(r"\D")


def normalize_phone(raw: str | None) -> str | None:
    """Indian mobile -> '+91XXXXXXXXXX', or None if invalid."""
    if raw is None:
        return None
    digits = _NON_DIGIT.sub("", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None


# ---------------------------------------------------------------- dates

_MONTHS = {
    name: number
    for number, name in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun",
         "jul", "aug", "sep", "oct", "nov", "dec"],
        start=1,
    )
}
_DATE_YMD = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
_DATE_DMY = re.compile(r"^(\d{1,2})([/-])(\d{1,2})\2(\d{4})$")
_DATE_DMONY = re.compile(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$")


def parse_date(raw: str | None) -> str | None:
    """Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY, DD-Mon-YYYY (day-first).

    Returns ISO 'YYYY-MM-DD', or None for unknown formats or impossible dates.
    """
    if raw is None:
        return None
    value = raw.strip()

    if m := _DATE_YMD.match(value):
        year, month, day = int(m[1]), int(m[2]), int(m[3])
    elif m := _DATE_DMY.match(value):
        day, month, year = int(m[1]), int(m[3]), int(m[4])
    elif m := _DATE_DMONY.match(value):
        month = _MONTHS.get(m[2].lower())
        if month is None:
            return None
        day, year = int(m[1]), int(m[3])
    else:
        return None

    try:
        return date(year, month, day).isoformat()
    except ValueError:  # e.g. 31/02, month 13
        return None


# ---------------------------------------------------------------- decimal

_CURRENCY_PREFIX = re.compile(r"^(?:₹|rs\.?|inr)\s*", re.IGNORECASE)
_PLAIN_NUMBER = re.compile(r"^(?:\d+(?:\.\d*)?|\.\d+)$")
_GROUPED_NUMBER = re.compile(r"^(?:\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})(?:\.\d*)?$")
_TWO_PLACES = Decimal("0.01")


def parse_decimal(raw: str | None) -> str | None:
    """'₹27,554' / 'Rs. 31629' / 'INR 25301.4' -> '27554.00' etc.

    Returned as a string with exactly 2 decimals (never float, to avoid
    rounding drift). Negative or non-numeric values return None.
    """
    if raw is None:
        return None
    value = _CURRENCY_PREFIX.sub("", raw.strip()).strip()
    if "," in value:
        if not _GROUPED_NUMBER.match(value):
            return None  # malformed separators, e.g. '1,2,3' or '12,34'
        value = value.replace(",", "")
    if not _PLAIN_NUMBER.match(value):
        return None  # covers '-', '-5', 'twelve thousand', ''
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return None
    return str(amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------- enum

def parse_enum(raw: str | None, allowed: list[str]) -> str | None:
    """Case-insensitive match; stored lower-case."""
    if raw is None:
        return None
    value = raw.strip().lower()
    return value if value in {a.lower() for a in allowed} else None


# ---------------------------------------------------------------- timestamps

def parse_timestamp(raw) -> datetime | None:
    """'...Z', '...+05:30' or naive (= IST) -> aware datetime in UTC."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)  # naive means IST
    return parsed.astimezone(timezone.utc)


def to_utc_string(moment: datetime) -> str:
    """Canonical storage form; sorts correctly as text."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_utc_string(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def to_ist_iso(moment: datetime) -> str:
    """e.g. '2026-10-14T17:55:24+05:30'."""
    return moment.astimezone(IST).strftime("%Y-%m-%dT%H:%M:%S+05:30")


def ist_date(moment: datetime) -> str:
    return moment.astimezone(IST).date().isoformat()