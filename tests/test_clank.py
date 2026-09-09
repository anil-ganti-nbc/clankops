import pytest

from clankops.errors import DuplicateError, NotFoundError


def test_register_and_resolve(store) -> None:
    row = store.register_clank(
        "OEM Radar",
        display_name="OEM Radar",
        aliases=["radar"],
        local_path=r"C:\Users\anil\Clanks\oem-radar",
    )
    assert row["slug"] == "oem-radar"
    same = store.resolve_clank("radar")
    assert same["clank_id"] == row["clank_id"]
    same2 = store.resolve_clank(row["clank_id"])
    assert same2["slug"] == "oem-radar"


def test_stable_identity_across_alias_and_path_change(store) -> None:
    row = store.register_clank("watch-clank", local_path=r"C:\old\watch-clank")
    cid = row["clank_id"]
    store.add_alias(cid, "horology")
    store.update_ref(cid, "local_path", r"C:\Users\anil\Clanks\watch-clank", replace_kind=True)
    again = store.resolve_clank("horology")
    assert again["clank_id"] == cid
    detail = store.clank_detail("watch-clank")
    paths = [r["ref_value"] for r in detail["refs"] if r["ref_kind"] == "local_path"]
    assert paths == [r"C:\Users\anil\Clanks\watch-clank"]


def test_duplicate_slug_rejected(store) -> None:
    store.register_clank("ctw")
    with pytest.raises(DuplicateError):
        store.register_clank("ctw")


def test_unknown_clank(store) -> None:
    with pytest.raises(NotFoundError):
        store.resolve_clank("missing")
