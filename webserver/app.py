#!/usr/bin/env python3
import io
import json
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gpiozero import DigitalOutputDevice, PWMOutputDevice


HOST = "0.0.0.0"
PORT = 8080
DEADMAN_SECONDS = 0.8
CAMERA_START_GRACE_SECONDS = 8.0
CAMERA_STALL_SECONDS = 5.0
WEB_ROOT = Path(__file__).with_name("static")


class Motor:
    def __init__(self, enable_pin, in1_pin, in2_pin):
        self.enable = PWMOutputDevice(enable_pin, frequency=1000, initial_value=0)
        self.in1 = DigitalOutputDevice(in1_pin, initial_value=False)
        self.in2 = DigitalOutputDevice(in2_pin, initial_value=False)

    def drive(self, direction, speed):
        self.enable.value = 0
        if direction == "forward":
            self.in1.on()
            self.in2.off()
        elif direction == "reverse":
            self.in1.off()
            self.in2.on()
        else:
            raise ValueError("direction must be forward or reverse")
        self.enable.value = max(0.0, min(float(speed), 1.0))

    def stop(self):
        self.enable.value = 0
        self.in1.off()
        self.in2.off()

    def close(self):
        self.stop()
        self.enable.close()
        self.in1.close()
        self.in2.close()


MOTOR_PINS = {
    "M1": {"enable": 18, "in1": 23, "in2": 24, "driver": "Controller 1 / A", "position": "hinten links", "reversed": True},
    "M2": {"enable": 12, "in1": 25, "in2": 16, "driver": "Controller 1 / B", "position": "vorne links", "reversed": False},
    "M3": {"enable": 13, "in1": 17, "in2": 27, "driver": "Controller 2 / A", "position": "vorne rechts", "reversed": False},
    "M4": {"enable": 21, "in1": 22, "in2": 5, "driver": "Controller 2 / B", "position": "hinten rechts (vorläufig)", "reversed": False},
}

motors = {
    name: Motor(pins["enable"], pins["in1"], pins["in2"])
    for name, pins in MOTOR_PINS.items()
}
deadlines = {name: 0.0 for name in motors}
motor_lock = threading.Lock()
shutdown_event = threading.Event()
process_started_at = time.monotonic()

DRIVE_COMMANDS = {
    # Logical wheel directions after applying each motor's reversed flag.
    "forward":      {"M1": "forward", "M2": "forward", "M3": "forward", "M4": "forward"},
    "backward":     {"M1": "reverse", "M2": "reverse", "M3": "reverse", "M4": "reverse"},
    "turn_left":    {"M1": "reverse", "M2": "reverse", "M3": "forward", "M4": "forward"},
    "turn_right":   {"M1": "forward", "M2": "forward", "M3": "reverse", "M4": "reverse"},
    "strafe_left":  {"M1": "forward", "M2": "reverse", "M3": "forward", "M4": "reverse"},
    "strafe_right": {"M1": "reverse", "M2": "forward", "M3": "reverse", "M4": "forward"},
}


def physical_direction(name, logical_direction):
    if MOTOR_PINS[name]["reversed"]:
        return "reverse" if logical_direction == "forward" else "forward"
    return logical_direction


def drive_command(command, speed):
    directions = DRIVE_COMMANDS[command]
    deadline = time.monotonic() + DEADMAN_SECONDS
    with motor_lock:
        for name, direction in directions.items():
            motors[name].drive(physical_direction(name, direction), speed)
            deadlines[name] = deadline


def drive_vector(forward, turn, strafe, speed):
    # Positive values mean forward, turn right and strafe right.
    values = {
        "M2": forward + turn + strafe,  # front left
        "M3": forward - turn - strafe,  # front right
        "M1": forward + turn - strafe,  # rear left
        "M4": forward - turn + strafe,  # rear right
    }
    peak = max(1.0, *(abs(value) for value in values.values()))
    deadline = time.monotonic() + DEADMAN_SECONDS
    with motor_lock:
        for name, value in values.items():
            value = value / peak * speed
            if abs(value) < 0.03:
                motors[name].stop()
                deadlines[name] = 0.0
                continue
            direction = "forward" if value > 0 else "reverse"
            motors[name].drive(physical_direction(name, direction), abs(value))
            deadlines[name] = deadline


def stop_all():
    with motor_lock:
        for name, motor in motors.items():
            motor.stop()
            deadlines[name] = 0.0


def watchdog():
    while not shutdown_event.wait(0.1):
        now = time.monotonic()
        with motor_lock:
            for name, deadline in deadlines.items():
                if deadline and now > deadline:
                    motors[name].stop()
                    deadlines[name] = 0.0
        camera_age = now - process_started_at
        no_camera_start = camera_age > CAMERA_START_GRACE_SECONDS and camera_output.frame_count < 2
        stalled_camera = (
            camera_output.last_frame_at > 0
            and now - camera_output.last_frame_at > CAMERA_STALL_SECONDS
        )
        if camera_error or no_camera_start or stalled_camera:
            reason = camera_error or (
                f"only {camera_output.frame_count} frame(s) after {camera_age:.1f}s"
                if no_camera_start
                else f"no new frame for {now - camera_output.last_frame_at:.1f}s"
            )
            print(f"Camera watchdog restarting service: {reason}", flush=True)
            stop_all()
            shutdown_event.set()
            break


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.frame_count = 0
        self.last_frame_at = 0.0
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = bytes(buf)
            self.frame_count += 1
            self.last_frame_at = time.monotonic()
            self.condition.notify_all()
        return len(buf)


camera = None
camera_output = StreamingOutput()
camera_error = None


def start_camera():
    global camera, camera_error
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput
        from libcamera import Transform

        camera = Picamera2()
        config = camera.create_video_configuration(
            main={"size": (640, 480), "format": "RGB888"},
            controls={"FrameRate": 20},
            transform=Transform(hflip=True, vflip=True),
        )
        camera.configure(config)
        camera.start_recording(JpegEncoder(q=80), FileOutput(camera_output))
    except Exception as exc:
        camera_error = f"{type(exc).__name__}: {exc}"


class Handler(BaseHTTPRequestHandler):
    server_version = "ServiceBotControl/1.0"

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}", flush=True)

    def send_json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.serve_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
        elif path == "/api/status":
            self.send_json(200, {
                "ok": True,
                "camera": camera_error or "ok",
                "camera_frames": camera_output.frame_count,
                "motors": MOTOR_PINS,
            })
        elif path == "/stream.mjpg":
            self.stream_camera()
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json(400, {"ok": False, "error": "invalid JSON"})
            return

        if self.path == "/api/motor":
            name = str(data.get("motor", ""))
            direction = str(data.get("direction", ""))
            try:
                speed = float(data.get("speed", 0.35))
                if name not in motors:
                    raise ValueError("unknown motor")
                if direction not in ("forward", "reverse"):
                    raise ValueError("unknown direction")
                speed = max(0.1, min(speed, 1.0))
                with motor_lock:
                    motors[name].drive(physical_direction(name, direction), speed)
                    deadlines[name] = time.monotonic() + DEADMAN_SECONDS
                self.send_json(200, {"ok": True, "motor": name})
            except (TypeError, ValueError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
        elif self.path == "/api/drive":
            command = str(data.get("command", ""))
            try:
                speed = float(data.get("speed", 0.35))
                if command not in DRIVE_COMMANDS:
                    raise ValueError("unknown drive command")
                speed = max(0.1, min(speed, 1.0))
                drive_command(command, speed)
                self.send_json(200, {"ok": True, "command": command})
            except (TypeError, ValueError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
        elif self.path == "/api/vector":
            try:
                forward = max(-1.0, min(float(data.get("forward", 0)), 1.0))
                turn = max(-1.0, min(float(data.get("turn", 0)), 1.0))
                strafe = max(-1.0, min(float(data.get("strafe", 0)), 1.0))
                speed = max(0.1, min(float(data.get("speed", 0.35)), 1.0))
                drive_vector(forward, turn, strafe, speed)
                self.send_json(200, {"ok": True})
            except (TypeError, ValueError) as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
        elif self.path == "/api/stop":
            name = data.get("motor")
            with motor_lock:
                if name in motors:
                    motors[name].stop()
                    deadlines[name] = 0.0
                else:
                    for motor_name, motor in motors.items():
                        motor.stop()
                        deadlines[motor_name] = 0.0
            self.send_json(200, {"ok": True})
        else:
            self.send_error(404)

    def serve_file(self, path, content_type):
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def stream_camera(self):
        if camera_error:
            self.send_json(503, {"ok": False, "error": camera_error})
            return
        self.send_response(200)
        self.send_header("Age", "0")
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
        self.end_headers()
        try:
            while not shutdown_event.is_set():
                with camera_output.condition:
                    camera_output.condition.wait(timeout=2)
                    frame = camera_output.frame
                if frame is None:
                    continue
                self.wfile.write(b"--FRAME\r\n")
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(frame)))
                self.end_headers()
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass


def shutdown(*_args):
    shutdown_event.set()
    stop_all()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    threading.Thread(target=watchdog, daemon=True).start()
    start_camera()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.timeout = 0.5
    print(f"ServiceBot control listening on http://{HOST}:{PORT}", flush=True)
    try:
        while not shutdown_event.is_set():
            server.handle_request()
    finally:
        shutdown()
        server.server_close()
        if camera is not None:
            camera.stop_recording()
            camera.close()
        for motor in motors.values():
            motor.close()
