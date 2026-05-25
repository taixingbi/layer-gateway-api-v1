"""Tests for message_feedback persistence."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.message_feedback_service import (
    _prepare_feedback_reason_for_db,
    insert_message_feedback,
)


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


def test_prepare_feedback_reason_maps_thumbs_up_to_metadata():
    """``thumbs_up`` is not a DB reason; store as metadata.rating."""
    reason, meta = _prepare_feedback_reason_for_db("thumbs_up", {})
    assert reason is None
    assert meta["rating"] == "thumbs_up"


def test_prepare_feedback_reason_keeps_not_factual():
    reason, meta = _prepare_feedback_reason_for_db("not_factual", {"rating": "thumbs_down"})
    assert reason == "not_factual"
    assert meta["rating"] == "thumbs_down"


def test_prepare_feedback_reason_unknown_maps_to_other():
    reason, meta = _prepare_feedback_reason_for_db("hallucination", {})
    assert reason == "other"
    assert meta["raw_feedback_reason"] == "hallucination"


@patch("app.services.message_feedback_service.resolve_assistant_model_name", return_value="qwen2.5-7b")
@patch("app.services.message_feedback_service._model_route_from_message")
@patch("app.services.message_feedback_service.persistence_enabled", return_value=True)
@patch("app.services.message_feedback_service._assert_conversation_owned")
@patch("app.services.message_feedback_service._table")
def test_insert_backfills_model_route(mock_table, _owned, _enabled, mock_ctx, _resolve_model):
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    mock_ctx.return_value = (None, "rag")
    mock_msg = MagicMock()
    mock_msg.execute.return_value = MagicMock(data=[{"id": mid}])
    chain = mock_table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value = mock_msg
    mock_insert_exec = MagicMock()
    mock_insert_exec.execute.return_value = MagicMock(
        data=[{"id": str(uuid.uuid4()), "message_id": mid, "conversation_id": cid, "metadata": {}}]
    )
    mock_table.return_value.insert.return_value.select.return_value = mock_insert_exec

    insert_message_feedback(
        "tok",
        "user_001",
        message_id=mid,
        conversation_id=cid,
        feedback_reason="not_factual",
        feedback=-1,
    )
    insert_row = mock_table.return_value.insert.call_args[0][0]
    assert insert_row["model"] == "qwen2.5-7b"
    assert insert_row["route"] == "rag"


@patch(
    "app.services.message_feedback_service._resolve_model_route_for_feedback",
    return_value=(None, None),
)
@patch("app.services.message_feedback_service.persistence_enabled", return_value=True)
@patch("app.services.message_feedback_service._assert_conversation_owned")
@patch("app.services.message_feedback_service._table")
def test_insert_message_feedback(mock_table, _owned, _enabled, _resolve_mr):
    """Insert builds row with feedback_reason and metadata."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    fid = str(uuid.uuid4())

    mock_msg = MagicMock()
    mock_msg.execute.return_value = MagicMock(data=[{"id": mid}])
    chain = mock_table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value = mock_msg

    row_data = {
        "id": fid,
        "message_id": mid,
        "conversation_id": cid,
        "user_id": "user_001",
        "feedback": -1,
        "feedback_reason": "other",
        "preference_score": 1,
        "reviewer_type": "end_user",
        "model": "qwen2.5-7b",
        "route": "rag",
        "feedback_comment": "Wrong visa answer",
        "labeler_notes": None,
        "metadata": {"rating": "thumbs_down", "raw_feedback_reason": "hallucination"},
        "created_at": "2026-05-20T13:20:11+00:00",
    }
    mock_insert_exec = MagicMock()
    mock_insert_exec.execute.return_value = MagicMock(data=[row_data])
    mock_insert_select = MagicMock()
    mock_insert_select.select.return_value = mock_insert_exec
    mock_table.return_value.insert.return_value = mock_insert_select

    row = insert_message_feedback(
        "tok",
        "user_001",
        message_id=mid,
        conversation_id=cid,
        feedback_reason="hallucination",
        feedback=-1,
        preference_score=1,
        model="qwen2.5-7b",
        route="rag",
        feedback_comment="Wrong visa answer",
        metadata={"rating": "thumbs_down"},
    )
    assert row["id"] == fid
    assert row["feedback"] == -1
    insert_row = mock_table.return_value.insert.call_args[0][0]
    assert insert_row["message_id"] == mid
    assert insert_row["feedback_reason"] == "other"
    assert insert_row["metadata"]["rating"] == "thumbs_down"
    assert insert_row["metadata"]["raw_feedback_reason"] == "hallucination"


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
@patch("app.routes.feedback.insert_message_feedback")
def test_post_feedback_thumbs_up_row_shape(mock_insert, _enabled):
    """Thumbs up: feedback_reason null, rating in metadata."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    mock_insert.return_value = {
        "id": fid,
        "message_id": mid,
        "conversation_id": cid,
        "feedback": 1,
        "metadata": {"rating": "thumbs_up"},
    }

    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/v1/feedback",
        headers=_auth_headers(),
        json={
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_up",
            "trace_id": "trace-1",
        },
    )
    assert response.status_code == 200
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs["feedback"] == 1
    assert call_kwargs["feedback_reason"] is None
    assert call_kwargs["metadata"]["rating"] == "thumbs_up"
    assert call_kwargs["preference_score"] == 5


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
@patch("app.routes.feedback.insert_message_feedback")
def test_post_feedback_thumbs_down_with_reason(mock_insert, _enabled):
    """Thumbs down + not_factual maps to feedback_reason column."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    mock_insert.return_value = {"id": str(uuid.uuid4()), "message_id": mid, "conversation_id": cid}

    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/v1/feedback",
        headers=_auth_headers(),
        json={
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_down",
            "feedback_type": "not_factual",
            "comment": "citation is incorrect",
        },
    )
    assert response.status_code == 200
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs["feedback"] == -1
    assert call_kwargs["feedback_reason"] == "not_factual"
    assert call_kwargs["metadata"]["rating"] == "thumbs_down"
    assert call_kwargs["feedback_comment"] == "citation is incorrect"


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=False)
def test_post_feedback_requires_supabase(_enabled):
    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/v1/feedback",
        headers=_auth_headers(),
        json={
            "message_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "rating": "thumbs_up",
            "trace_id": "t1",
        },
    )
    assert response.status_code == 503


@patch("app.services.message_feedback_service.persistence_enabled", return_value=True)
@patch("app.services.message_feedback_service._assert_conversation_owned")
@patch("app.services.message_feedback_service._table")
def test_insert_rejects_invalid_preference_score(mock_table, _owned, _enabled):
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    mock_msg = MagicMock()
    mock_msg.execute.return_value = MagicMock(data=[{"id": mid}])
    chain = mock_table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value = mock_msg

    with pytest.raises(HTTPException) as exc:
        insert_message_feedback(
            "tok",
            "user_001",
            message_id=mid,
            conversation_id=cid,
            preference_score=9,
        )
    assert exc.value.status_code == 400
