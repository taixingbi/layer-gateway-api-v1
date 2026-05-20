"""FeedbackRequest validation (422 edge cases from BFF/UI)."""

import uuid

from app.schemas.feedback import FeedbackRequest


def test_accepts_run_id_and_reason_extra_fields():
    """BFF may forward ``run_id``; UI may send ``reason`` — both are ignored or mapped."""
    mid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    req = FeedbackRequest.model_validate(
        {
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_down",
            "run_id": "trace-from-ui",
            "reason": "not_factually_correct",
        }
    )
    assert req.trace_id == "trace-from-ui"
    assert req.feedback == -1


def test_empty_trace_id_and_null_metadata():
    """Empty trace_id and null metadata must not 422."""
    mid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    req = FeedbackRequest.model_validate(
        {
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_up",
            "trace_id": "",
            "metadata": None,
        }
    )
    assert req.trace_id is None
    assert req.metadata == {}
    assert req.feedback == 1


def test_legacy_trace_only_validates():
    """Orchestrator-only body (no message ids) must not 422."""
    req = FeedbackRequest.model_validate(
        {"trace_id": "trace-from-ui", "rating": "thumbs_up"},
    )
    assert req.message_id is None
    assert req.trace_id == "trace-from-ui"


def test_db_prefix_stripped_from_message_id():
    mid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    req = FeedbackRequest.model_validate(
        {
            "message_id": f"db-{mid}",
            "conversation_id": cid,
            "rating": "thumbs_up",
        }
    )
    assert req.message_id == mid
