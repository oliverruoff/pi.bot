import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request("http://127.0.0.1:18080" + path, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as exc:
        return exc.code, json.load(exc)


def test_mock_hardware_endpoints_and_watchdog():
    env = {**os.environ, "PI_BOT_MOCK_HARDWARE": "1", "PI_BOT_DISABLE_AGENT": "1", "PI_BOT_PORT": "18080"}
    process = subprocess.Popen([sys.executable, str(ROOT / "webserver" / "app.py")], env=env)
    try:
        for _ in range(30):
            try:
                if request("/api/status")[0] == 200: break
            except OSError: time.sleep(.05)
        assert request("/api/drive", {"command": "forward", "speed": .3})[0] == 200
        assert request("/api/drive/status")[1]["active"] is True
        time.sleep(1)
        assert request("/api/drive/status")[1]["active"] is False
        assert request("/api/agent/vector", {"forward": 1, "turn": 0, "strafe": 0, "speed": .2})[0] == 200
        assert request("/api/stop", {})[0] == 200
    finally:
        process.terminate(); process.wait(timeout=3)
