import uuid

import pytest

from clankops.ids import format_mission_display_id, is_uuid, new_id, parse_mission_display_n


def test_uuidv7_shape_and_uniqueness() -> None:
    ids = [new_id() for _ in range(50)]
    assert len(set(ids)) == 50
    for value in ids:
        parsed = uuid.UUID(value)
        assert parsed.version == 7
        assert is_uuid(value)


def test_mission_display_id() -> None:
    assert format_mission_display_id(1) == "COPS-000001"
    assert format_mission_display_id(123) == "COPS-000123"


def test_parse_mission_display_n_whole_token_after_strip() -> None:
    assert parse_mission_display_n("  COPS-000007  ") == 7
    assert parse_mission_display_n("COPS-000007 extra") is None
    assert parse_mission_display_n("id=COPS-000007") is None
    assert parse_mission_display_n("") is None


@pytest.mark.parametrize("sequence", [0, -1])
def test_mission_display_id_rejects_nonpositive_sequence(sequence: int) -> None:
    with pytest.raises(ValueError, match=r"mission display sequence must be >= 1"):
        format_mission_display_id(sequence)
