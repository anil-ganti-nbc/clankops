import sys
from pathlib import Path

import clankops.ids as ids
from clankops.ids import MIN_PYTHON, assert_python_contract, new_id


def test_runtime_is_python_314_or_newer() -> None:
    assert sys.version_info >= MIN_PYTHON
    assert_python_contract()


def test_packaging_requires_python_314() -> None:
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.14"' in text
    assert ">=3.13" not in text.split("requires-python", 1)[1].splitlines()[0]


def test_uuidv7_stdlib_required_no_uuid4_fallback() -> None:
    import uuid

    assert hasattr(uuid, "uuid7")
    value = new_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert ids.new_id.__doc__ is None or "UUIDv7" in (ids.new_id.__doc__ or "")


def test_parse_mission_display_n() -> None:
    from clankops.ids import parse_mission_display_n

    assert parse_mission_display_n("COPS-000001") == 1
    assert parse_mission_display_n("COPS-000123") == 123
    assert parse_mission_display_n("nope") is None
    assert parse_mission_display_n(None) is None
