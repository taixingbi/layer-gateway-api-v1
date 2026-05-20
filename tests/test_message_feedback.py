"""Tests for message_feedback persistence."""

import uuid
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from app.services.message_feedback_service import insert_message_feedback


def _auth_headers():
    return {"Authorization": "Bearer token-123"}


@patch("app.services.message_feedback_service.persistence_enabled", return_value=True)
@patch("app.services.message_feedback_service._assert_conversation_owned")
@patch("app.services.message_feedback_service._table")
def test_insert_message_feedback(mock_table, _owned, _enabled):
    """Insert builds row with feedback scores and metadata."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    fid = str(uuid.uuid4())

    mock_msg = MagicMock()
    mock_msg.execute.return_value = MagicMock(data=[{"id": mid}])
    chain = mock_table.return_value.select.return_value
    chain.eq.return_value = chain
    chain.limit.return_value = mock_msg

    mock_insert = MagicMock()
    mock_insert.execute.return_value = MagicMock(
        data=[
            {
                "id": fid,
                "message_id": mid,
                "conversation_id": cid,
                "user_id": "user_001",
                "feedback": -1,
                "feedback_type": "hallucination",
                "preference_score": 1,
                "reviewer_type": "end_user",
                "model": "qwen2.5-7b",
                "route": "rag",
                "feedback_comment": "Wrong visa answer",
                "labeler_notes": None,
                "metadata": {"latency_ms": 1820},
                "created_at": "2026-05-20T13:20:11+00:00",
            }
        ]
    )
    mock_table.return_value.insert.return_value = mock_insert

    row = insert_message_feedback(
        "tok",
        "user_001",
        message_id=mid,
        conversation_id=cid,
        feedback_type="hallucination",
        feedback=-1,
        preference_score=1,
        model="qwen2.5-7b",
        route="rag",
        feedback_comment="Wrong visa answer",
        metadata={"latency_ms": 1820},
    )
    assert row["id"] == fid
    assert row["feedback"] == -1
    insert_row = mock_table.return_value.insert.call_args[0][0]
    assert insert_row["message_id"] == mid
    assert insert_row["metadata"]["latency_ms"] == 1820


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=True)
@patch("app.routes.feedback.insert_message_feedback")
def test_post_feedback_returns_created_row(mock_insert, _enabled):
    """POST /api/feedback persists and returns FeedbackResponse."""
    cid = str(uuid.uuid4())
    mid = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    mock_insert.return_value = {
        "id": fid,
        "message_id": mid,
        "conversation_id": cid,
        "feedback": 1,
        "feedback_type": "thumbs_up",
    }

    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={
            "message_id": mid,
            "conversation_id": cid,
            "rating": "thumbs_up",
            "trace_id": "trace-1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == fid
    assert body["message_id"] == mid
    assert body["status"] == "created"
    call_kwargs = mock_insert.call_args.kwargs
    assert call_kwargs["feedback"] == 1
    assert call_kwargs["feedback_type"] == "thumbs_up"
    assert call_kwargs["preference_score"] == 5


@patch("app.routes.feedback.feedback_persistence_enabled", return_value=False)
def test_post_feedback_requires_supabase_when_not_proxy_only(_enabled, monkeypatch):
    """Without Supabase persistence, return 503 (gateway_json has no orchestrator proxy)."""
    monkeypatch.setenv("ORCHESTRATOR_CONTRACT", "gateway_json")
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)
    response = client.post(
        "/api/feedback",
        headers=_auth_headers(),
        json={
            "message_id": str(uuid.uuid4()),
            "conversation_id": str(uuid.uuid4()),
            "rating": "thumbs_up",
            "trace_id": "t1",
        },
    )
    assert response.status_code == 503
    get_settings.cache_clear()


@patch("app.services.message_feedback_service.persistence_enabled", return_value=True)
@patch("app.services.message_feedback_service._assert_conversation_owned")
@patch("app.services.message_feedback_service._table")
def test_insert_rejects_invalid_preference_score(mock_table, _owned, _enabled):
    """preference_score outside 1..5 is rejected."""
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
