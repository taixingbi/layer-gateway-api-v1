"""Gateway settings defaults."""

from app.core.config import Settings


def test_orchestrator_chat_path_default():
    assert Settings.model_fields["orchestrator_chat_path"].default == "/v1/orchestrator/answer"
