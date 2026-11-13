from solution.pipeline.leads import read_raw_rows, validate_row

SCHEMA = {
    "workflow_id": "motor_renewal",
    "fields": [
        {"name": "customer_name", "type": "string", "required": True, "pii": True, "aliases": ["name"]},
        {"name": "phone", "type": "phone", "required": True, "pii": True, "aliases": ["mobile"]},
        {"name": "policy_number", "type": "string", "required": True, "reference": True},
        {"name": "policy_expiry", "type": "date", "required": False},
        {"name": "premium_amount", "type": "decimal", "required": False},
        {"name": "preferred_language", "type": "enum", "values": ["en", "hi"], "default": "en"},
    ],
}


def row(**overrides):
    base = {"customer_name": "A", "phone": "9876543210", "policy_number": "P1",
            "policy_expiry": "2026-11-23", "premium_amount": "100", "preferred_language": "hi"}
    base.update(overrides)
    return base


def test_valid_row():
    result = validate_row(row(), SCHEMA)
    assert not result.rejected and result.warnings == 0
    assert result.values["phone"] == "+919876543210"


def test_first_failure_in_schema_order_wins():
    # name missing AND phone invalid -> name comes first in the schema
    result = validate_row(row(customer_name=None, phone="123"), SCHEMA)
    assert result.reason == "missing_required:customer_name"


def test_invalid_phone_and_missing_phone():
    assert validate_row(row(phone="5831800366"), SCHEMA).reason == "invalid_phone"
    assert validate_row(row(phone=None), SCHEMA).reason == "missing_required:phone"


def test_invalid_optional_fields_become_null_with_warnings():
    result = validate_row(row(policy_expiry="31/02/2026", premium_amount="-", preferred_language="Hindi"), SCHEMA)
    assert not result.rejected
    assert result.warnings == 3
    assert result.values["policy_expiry"] is None


def test_warnings_not_counted_on_rejected_rows():
    result = validate_row(row(policy_number=None, premium_amount="abc"), SCHEMA)
    assert result.rejected and result.warnings == 0


def test_blank_rows_skipped_and_row_numbers_skip_them():
    text = "name,mobile,policy_number\nA,9876543210,P1\n,,\n   ,  , \nB,9876543211,P2\n"
    rows = list(read_raw_rows(text, SCHEMA))
    assert [n for n, _ in rows] == [1, 2]
    assert rows[1][1]["customer_name"] == "B"


def test_whitespace_only_cell_is_missing():
    text = "name,mobile,policy_number\n   ,9876543210,P1\n"
    (_, raw), = read_raw_rows(text, SCHEMA)
    assert validate_row(raw, SCHEMA).reason == "missing_required:customer_name"