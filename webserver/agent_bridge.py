"""Small persistent web-to-pi RPC bridge."""

from __future__ import annotations

import json
import os
import queue
import shlex
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any


COMPACTION_INSTRUCTIONS = """Preserve a concise operational summary of user goals,
robot state, important decisions, active tasks, safety concerns, and relevant skills.
Discard verbose intermediate chatter and stale tool output."""


def _assistant_text(message: dict[str, Any]) -> str:
    content = message.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    ).strip()


class PiProcess:
    def __init__(self, cwd: Path, skills_dir: Path, event_handler):
        self.cwd = cwd
        self.event_handler = event_handler
        command = os.getenv("PI_COMMAND", "pi")
        extra = shlex.split(os.getenv("PI_ARGS", ""))
        extension = cwd / "extensions" / "robot-tools.ts"
        self.argv = [command, "--mode", "rpc", "--skill", str(skills_dir), "--extension", str(extension), *extra]
        self.proc: subprocess.Popen[str] | None = None
        self.pending: dict[str, queue.Queue] = {}
        self.lock = threading.Lock()
        self.agent_done = threading.Event()

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            self.argv, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self.command({"type": "set_auto_compaction", "enabled": True})

    def stop(self) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            self.proc.kill()
        self.proc = None

    def command(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        if timeout is None:
            timeout = float(os.getenv("PI_PROMPT_TIMEOUT", "600")) if payload.get("type") == "prompt" else 120.0
        self.start()
        is_prompt = payload.get("type") == "prompt"
        if is_prompt:
            self.agent_done.clear()
        request_id = str(uuid.uuid4())
        response: queue.Queue = queue.Queue(maxsize=1)
        self.pending[request_id] = response
        line = json.dumps({**payload, "id": request_id}, ensure_ascii=False) + "\n"
        try:
            assert self.proc and self.proc.stdin
            with self.lock:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
            result = response.get(timeout=timeout)
            if not result.get("success", False):
                raise RuntimeError(result.get("error", "pi RPC command failed"))
            data = result.get("data") or {}
            if is_prompt and not self.agent_done.wait(timeout=timeout):
                raise TimeoutError("pi agent did not finish in time")
            return data
        finally:
            self.pending.pop(request_id, None)

    def _read_stdout(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = event.get("id")
            if event.get("type") == "response" and request_id in self.pending:
                self.pending[request_id].put(event)
            else:
                self.event_handler(event)
                if event.get("type") == "agent_end":
                    self.agent_done.set()

    def _read_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            print(f"pi: {line.rstrip()}", flush=True)


class ChatManager:
    """Serializes prompts while exposing durable UI history."""

    def __init__(self, project_root: Path):
        self.root = project_root
        self.data_dir = Path(os.getenv("NAVIBOT_DATA_DIR", project_root / "data")).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.data_dir / "chat-history.json"
        self.archive_dir = self.data_dir / "chat-archive"
        self.archive_dir.mkdir(exist_ok=True)
        self.history: list[dict[str, Any]] = self._load_history()
        self.busy = False
        self.status = "Bereit"
        self.revision = 0
        self.condition = threading.Condition()
        self.work: queue.Queue[str | None] = queue.Queue()
        skills = Path(os.getenv("NAVIBOT_SKILLS_DIR", project_root / "skills")).resolve()
        self.pi = PiProcess(project_root, skills, self._on_event)
        threading.Thread(target=self._worker, daemon=True).start()

    def _load_history(self) -> list[dict[str, Any]]:
        try:
            value = json.loads(self.history_file.read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self) -> None:
        tmp = self.history_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.history_file)

    def _changed(self) -> None:
        with self.condition:
            self.revision += 1
            self.condition.notify_all()

    def submit(self, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("message must not be empty")
        if text == "/new":
            self.new_chat()
            return
        self.history.append({"role": "user", "text": text, "time": int(time.time())})
        self._save()
        self.work.put(text)
        self._changed()

    def new_chat(self) -> None:
        if self.busy:
            raise RuntimeError("Der Agent arbeitet noch. Bitte zuerst stoppen.")
        self.pi.command({"type": "new_session"})
        if self.history:
            archive = self.archive_dir / f"{int(time.time())}-{uuid.uuid4().hex[:8]}.json"
            archive.write_text(json.dumps(self.history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.history = []
        self.status = "Neuer Chat"
        self._save()
        self._changed()

    def archives(self) -> list[dict[str, Any]]:
        result = []
        for path in sorted(self.archive_dir.glob("*.json"), reverse=True):
            try:
                messages = json.loads(path.read_text(encoding="utf-8"))
                first = next((m.get("text", "") for m in messages if m.get("role") == "user"), "Unterhaltung")
                result.append({"id": path.stem, "title": first[:80], "messages": messages})
            except (OSError, json.JSONDecodeError):
                continue
        return result

    def abort(self) -> None:
        try:
            self.pi.command({"type": "abort"}, timeout=3)
        finally:
            self.busy = False
            self.status = "Gestoppt"
            self._changed()

    def snapshot(self, after: int = -1, timeout: float = 0) -> dict[str, Any]:
        if timeout and after == self.revision:
            with self.condition:
                self.condition.wait_for(lambda: self.revision != after, timeout=min(timeout, 25))
        return {"revision": self.revision, "busy": self.busy, "status": self.status, "messages": self.history}

    def _estimated_tokens(self) -> int:
        return sum(len(item.get("text", "")) for item in self.history) // 4

    def _maybe_compact(self) -> None:
        window = max(1000, int(os.getenv("PI_CONTEXT_WINDOW", "200000")))
        ratio = min(0.95, max(0.25, float(os.getenv("PI_COMPACTION_RATIO", "0.80"))))
        if self._estimated_tokens() >= window * ratio:
            self.status = "Kontext wird kompaktiert …"
            self._changed()
            self.pi.command({"type": "compact", "customInstructions": COMPACTION_INSTRUCTIONS})

    def _worker(self) -> None:
        while True:
            text = self.work.get()
            if text is None:
                return
            self.busy = True
            self.status = "Denkt nach …"
            self._changed()
            try:
                self._maybe_compact()
                self.pi.command({"type": "prompt", "message": text})
            except Exception as exc:
                self.history.append({"role": "error", "text": str(exc), "time": int(time.time())})
                self._save()
            finally:
                self.busy = False
                self.status = "Bereit"
                self._changed()
                self.work.task_done()

    def _on_event(self, event: dict[str, Any]) -> None:
        typ = event.get("type")
        if typ == "tool_execution_start":
            self.status = f"Aktion: {event.get('toolName', 'Tool')}"
            self._changed()
        elif typ == "message_end":
            message = event.get("message") or {}
            if message.get("role") == "assistant":
                text = _assistant_text(message)
                if text:
                    self.history.append({"role": "assistant", "text": text, "time": int(time.time())})
                    self._save()
                    self._changed()
