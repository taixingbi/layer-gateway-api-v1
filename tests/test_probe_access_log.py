"""Access logs on probe paths include correlation fields; probes stay unauthenticated."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import create_app


def _request_complete_kwargs(mock_log_event):
    for call in mock_log_event.call_args_list:
        if call.args and call.args[0] == "request_complete":
            return call.kwargs
    return None


def test_health_request_complete_with_optional_headers_still_omits_probe_correlation_fields():
    """Probe routes do not attach request/trace/session to access-log state; headers are ignored for ``request_complete``."""
    with patch("app.middleware.access_log.log_event") as mock_log:
        app = create_app()
        with TestClient(app) as client:
            response = client.get(
                "/health",
                headers={
                    "X-Session-Id": "probe-sess-001",
                    "X-Request-Id": "probe-req-001",
                    "X-Trace-Id": "probe-trace-001",
                },
            )
        assert response.status_code == 200
    assert "x-request-id" not in {k.lower() for k in response.headers.keys()}
    assert "x-trace-id" not in {k.lower() for k in response.headers.keys()}

    fields = _request_complete_kwargs(mock_log)
    assert fields is not None
    assert fields["path"] == "/health"
    assert "request_id" not in fields
    assert "trace_id" not in fields
    assert "session_id" not in fields


def test_health_request_complete_mints_ids_when_headers_absent():
    with patch("app.middleware.access_log.log_event") as mock_log:
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/health")
        assert response.status_code == 200

    fields = _request_complete_kwargs(mock_log)
    assert fields is not None
    assert fields["path"] == "/health"
    assert "request_id" not in fields
    assert "trace_id" not in fields
    assert "x_session_id" not in fields
    assert "session_id" not in fields


def test_metrics_request_complete_includes_correlation_fields():
    with patch("app.middleware.access_log.log_event") as mock_log:
        app = create_app()
        with TestClient(app) as client:
            response = client.get("/metrics", headers={"X-Request-Id": "m-req-1", "X-Trace-Id": "m-tr-1"})
        assert response.status_code == 200

    fields = _request_complete_kwargs(mock_log)
    assert fields is not None
    assert fields["path"] == "/metrics"
    assert "request_id" not in fields
    assert "trace_id" not in fields
