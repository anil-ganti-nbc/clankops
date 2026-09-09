import uuid

from clankops.ids import format_mission_display_id, is_uuid, new_id


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
