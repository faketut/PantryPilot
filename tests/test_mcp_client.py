"""Regression tests for the mcp_client filter helpers."""
from app.mcp_client import _oid_filter


def test_oid_filter_passes_uuid_string_through():
    """Pantry items use UUID strings as _id, which aren't valid ObjectIds.
    The helper must leave them alone instead of raising."""
    uid = "d0d40db5-54d2-484a-9541-9c4c9697cb2e"
    out = _oid_filter({"_id": uid})
    assert out == {"_id": uid}


def test_oid_filter_in_clause_handles_uuid_strings():
    """The $in branch previously crashed when any operand wasn't a valid
    ObjectId. UUIDs must pass through untouched."""
    uids = [
        "d0d40db5-54d2-484a-9541-9c4c9697cb2e",
        "8e6ea03c-9415-454b-a99b-277105f4d093",
    ]
    out = _oid_filter({"_id": {"$in": uids}})
    assert out == {"_id": {"$in": uids}}


def test_oid_filter_converts_valid_objectid_hex():
    """A real 24-char ObjectId hex should still be coerced for callers that
    actually have ObjectIds in their collections."""
    from bson import ObjectId
    hex_id = "507f1f77bcf86cd799439011"
    out = _oid_filter({"_id": hex_id})
    assert isinstance(out["_id"], ObjectId)
    assert str(out["_id"]) == hex_id


def test_oid_filter_in_clause_mixes_objectid_and_uuid():
    from bson import ObjectId
    hex_id = "507f1f77bcf86cd799439011"
    uid = "d0d40db5-54d2-484a-9541-9c4c9697cb2e"
    out = _oid_filter({"_id": {"$in": [hex_id, uid]}})
    in_clause = out["_id"]["$in"]
    assert isinstance(in_clause[0], ObjectId)
    assert in_clause[1] == uid
