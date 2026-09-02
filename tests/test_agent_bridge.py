import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "webserver"))

from agent_bridge import ChatManager, _assistant_text


def test_assistant_text_extracts_only_text_parts():
    message = {"content": [{"type": "text", "text": "Hallo"}, {"type": "image", "data": "x"}]}
    assert _assistant_text(message) == "Hallo"


def test_compaction_is_model_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("PI_BOT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PI_CONTEXT_WINDOW", "1000")
    monkeypatch.setenv("PI_COMPACTION_RATIO", "0.5")
    manager = ChatManager.__new__(ChatManager)
    manager.history = [{"text": "x" * 2000}]
    assert manager._estimated_tokens() == 500

