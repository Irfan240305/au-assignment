from datetime import datetime, timezone

import pytest

from solution.pipeline.normalize import (build_header_map, ist_date, normalize_header, normalize_phone,
                                         parse_date, parse_decimal, parse_enum, parse_timestamp, to_ist_iso)


@pytest.mark.parametrize("raw, expected", [
    ("+916463745499", "+916463745499"),
    ("9932302346", "+919932302346"),
    ("+91 79328 96340", "+917932896340"),
    ("(+91) 8196106462", "+918196106462"),
    ("91-81518-64492", "+918151864492"),
    ("06832582386", "+916832582386"),
    ("+1 415 555 0134", None),      # foreign number
    ("022-2006-8955", None),        # landline: starts with 2 after dropping 0
    ("5831800366", None),           # must start with 6-9
    ("883958558", None),            # 9 digits
    ("919876543210", "+919876543210"),
    ("", None),
    (None, None),
])
def test_normalize_phone(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("2026-11-23", "2026-11-23"),
    ("21-11-2026", "2026-11-21"),
    ("27/10/2026", "2026-10-27"),
    ("29-Dec-2026", "2026-12-29"),
    ("01-dec-2026", "2026-12-01"),   # month name is case-insensitive
    ("05/11/2026", "2026-11-05"),    # day-first, not 11 May
    ("31/02/2026", None),            # impossible date
    ("2026-13-01", None),            # month 13
    ("29-Foo-2026", None),
    ("2026/11/23", None),            # unsupported format
    ("", None),
    (None, None),
])
def test_parse_date(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize("raw, expected", [
    ("₹27,554", "27554.00"),
    ("6850", "6850.00"),
    ("29678.29", "29678.29"),
    ("INR 25301.4", "25301.40"),
    ("Rs. 31629", "31629.00"),
    ("rs 1,00,000", "100000.00"),
    ("Rs.500", "500.00"),
    ("10.005", "10.01"),             # half-up rounding
    ("27,554", "27554.00"),          # Western grouping
    ("12,34,567.5", "1234567.50"),   # Indian grouping
    ("1,2,3", None),                 # malformed separators
    ("12,34", None),
    ("-", None),
    ("-5", None),                    # must be >= 0
    ("twelve thousand", None),
    (None, None),
])
def test_parse_decimal(raw, expected):
    assert parse_decimal(raw) == expected


def test_parse_enum():
    allowed = ["en", "hi", "ta"]
    assert parse_enum("TA", allowed) == "ta"
    assert parse_enum(" hi ", allowed) == "hi"
    assert parse_enum("Hindi", allowed) is None
    assert parse_enum(None, allowed) is None


def test_header_normalisation_and_mapping():
    assert normalize_header("  Mobile_No ") == "mobile no"
    assert normalize_header("Insured   Name") == "insured name"
    fields = [
        {"name": "customer_name", "aliases": ["name", "insured name"]},
        {"name": "phone", "aliases": ["mobile no"]},
    ]
    headers = ["Language", "Insured Name", "Mobile No", "agent_notes", "phone"]
    # extra columns ignored; first matching column wins for a field
    assert build_header_map(headers, fields) == {"customer_name": 1, "phone": 2}


def test_timestamps_naive_means_ist():
    utc = timezone.utc
    assert parse_timestamp("2026-10-01 09:15:55") == datetime(2026, 10, 1, 3, 45, 55, tzinfo=utc)
    assert parse_timestamp("2026-10-01T09:15:55Z") == datetime(2026, 10, 1, 9, 15, 55, tzinfo=utc)
    assert parse_timestamp("2026-10-01T09:15:55+05:30") == datetime(2026, 10, 1, 3, 45, 55, tzinfo=utc)
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(None) is None


def test_ist_date_crosses_midnight():
    late_utc = datetime(2026, 10, 1, 20, 0, 0, tzinfo=timezone.utc)  # 01:30 IST next day
    assert ist_date(late_utc) == "2026-10-02"
    assert to_ist_iso(late_utc) == "2026-10-02T01:30:00+05:30"