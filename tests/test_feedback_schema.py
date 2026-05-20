"""FeedbackRequest validation (422 edge cases from BFF/UI)."""

import uuid

from app.schemas.feedback import FeedbackRequest


def test_accepts_run_id_and_maps_feedback_type_to_reason():
    mid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    req = FeedbackRequest.model_validate(
        {
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_down",
            "run_id": "trace-from-ui",
            "feedback_type": "not_factual",
        }
    )
    assert req.trace_id == "trace-from-ui"
    assert req.feedback == -1
    assert req.feedback_reason == "not_factual"


def test_empty_trace_id_and_null_metadata():
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
    assert req.metadata == {"rating": "thumbs_up"}
    assert req.feedback == 1
    assert req.feedback_reason is None


def test_legacy_trace_only_validates():
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


def test_thumbs_up_strips_client_feedback_reason():
    mid = str(uuid.uuid4())
    cid = str(uuid.uuid4())
    req = FeedbackRequest.model_validate(
        {
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_up",
            "feedback_type": "thumbs_up",
        }
    )
    assert req.feedback_reason is None
    assert req.metadata["rating"] == "thumbs_up"
