#!/usr/bin/env python3
"""pi.bot hardware owner, camera streamer, and web/agent gateway."""
from __future__ import annotations
import io, json, os, signal, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from agent_bridge import ChatManager

HOST=os.getenv("PI_BOT_HOST","0.0.0.0"); PORT=int(os.getenv("PI_BOT_PORT","8080")); DEADMAN_SECONDS=float(os.getenv("PI_BOT_DEADMAN_SECONDS","0.8"))
WEB_ROOT=Path(__file__).with_name("static"); PROJECT_ROOT=Path(__file__).resolve().parent.parent; MOCK=os.getenv("PI_BOT_MOCK_HARDWARE")=="1"
shutdown_event=threading.Event(); process_started_at=time.monotonic(); motor_lock=threading.Lock()

class MockPin:
    value=0
    def on(self): self.value=1
    def off(self): self.value=0
    def close(self): pass
class Motor:
    def __init__(self,en,i1,i2):
        if MOCK: self.enable=self.in1=self.in2=MockPin()
        else:
            from gpiozero import DigitalOutputDevice,PWMOutputDevice
            self.enable=PWMOutputDevice(en,frequency=1000,initial_value=0); self.in1=DigitalOutputDevice(i1,initial_value=False); self.in2=DigitalOutputDevice(i2,initial_value=False)
    def drive(self,direction,speed):
        self.enable.value=0
        if direction=="forward": self.in1.on(); self.in2.off()
        elif direction=="reverse": self.in1.off(); self.in2.on()
        else: raise ValueError("direction must be forward or reverse")
        self.enable.value=max(0.,min(float(speed),1.))
    def stop(self): self.enable.value=0; self.in1.off(); self.in2.off()
    def close(self): self.stop(); self.enable.close(); self.in1.close(); self.in2.close()

MOTOR_PINS={"M1":{"enable":18,"in1":23,"in2":24,"position":"hinten links","reversed":True},"M2":{"enable":12,"in1":25,"in2":16,"position":"vorne links","reversed":False},"M3":{"enable":13,"in1":17,"in2":27,"position":"vorne rechts","reversed":False},"M4":{"enable":21,"in1":22,"in2":5,"position":"hinten rechts","reversed":False}}
motors={n:Motor(p["enable"],p["in1"],p["in2"]) for n,p in MOTOR_PINS.items()}; deadlines={n:0. for n in motors}
drive_state={"active":False,"source":None,"command":"stop","speed":0.,"deadline":None}
manual_priority_until=0.0
DRIVE_COMMANDS={"forward":{"M1":1,"M2":1,"M3":1,"M4":1},"backward":{"M1":-1,"M2":-1,"M3":-1,"M4":-1},"turn_left":{"M1":-1,"M2":-1,"M3":1,"M4":1},"turn_right":{"M1":1,"M2":1,"M3":-1,"M4":-1},"strafe_left":{"M1":1,"M2":-1,"M3":1,"M4":-1},"strafe_right":{"M1":-1,"M2":1,"M3":-1,"M4":1}}
def physical_direction(name,value):
    forward=value>0
    if MOTOR_PINS[name]["reversed"]: forward=not forward
    return "forward" if forward else "reverse"
def apply_drive(values,speed,source,command):
    deadline=time.monotonic()+DEADMAN_SECONDS; peak=max(1.,*(abs(v) for v in values.values()))
    with motor_lock:
        for name,value in values.items():
            value=value/peak*speed
            if abs(value)<.03: motors[name].stop(); deadlines[name]=0.
            else: motors[name].drive(physical_direction(name,value),abs(value)); deadlines[name]=deadline
        drive_state.update(active=True,source=source,command=command,speed=speed,deadline=time.time()+DEADMAN_SECONDS)
def stop_all():
    with motor_lock:
        for name,motor in motors.items(): motor.stop(); deadlines[name]=0.
        drive_state.update(active=False,source=None,command="stop",speed=0.,deadline=None)

class StreamingOutput(io.BufferedIOBase):
    def __init__(self): self.frame=None; self.frame_count=0; self.last_frame_at=0.; self.condition=threading.Condition()
    def write(self,buf):
        with self.condition: self.frame=bytes(buf); self.frame_count+=1; self.last_frame_at=time.monotonic(); self.condition.notify_all()
        return len(buf)
camera=None; camera_output=StreamingOutput(); camera_error=None
def start_camera():
    global camera,camera_error
    if MOCK: camera_error="mock mode"; return
    try:
        from picamera2 import Picamera2
        from picamera2.encoders import JpegEncoder
        from picamera2.outputs import FileOutput
        from libcamera import Transform
        camera=Picamera2(); camera.configure(camera.create_video_configuration(main={"size":(640,480),"format":"RGB888"},controls={"FrameRate":20},transform=Transform(hflip=True,vflip=True))); camera.start_recording(JpegEncoder(q=80),FileOutput(camera_output))
    except Exception as exc: camera_error=f"{type(exc).__name__}: {exc}"

chat=None
class Handler(BaseHTTPRequestHandler):
    server_version="pi.bot/2.0"
    def log_message(self,fmt,*args): print(f"{self.address_string()} - {fmt%args}",flush=True)
    def send_json(self,status,payload):
        body=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type","application/json; charset=utf-8"); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(body)
    def body(self): return json.loads(self.rfile.read(int(self.headers.get("Content-Length","0"))) or b"{}")
    def do_GET(self):
        parsed=urlparse(self.path); path=parsed.path
        if path=="/": return self.serve_file(WEB_ROOT/"index.html","text/html; charset=utf-8")
        if path=="/stream.mjpg": return self.stream_camera()
        if path=="/api/snapshot": return self.snapshot()
        if path=="/api/drive/status": return self.send_json(200,{"ok":True,**drive_state})
        if path=="/api/status": return self.send_json(200,{"ok":True,"camera":camera_error or "ok","camera_frames":camera_output.frame_count,"uptime_seconds":round(time.monotonic()-process_started_at),"drive":drive_state})
        if path=="/api/chat":
            q=parse_qs(parsed.query); after=int(q.get("after",[-1])[0]); timeout=float(q.get("timeout",[0])[0]); state=chat.snapshot(after,timeout) if chat else {"busy":False,"status":"Agent deaktiviert","messages":[],"revision":0}; return self.send_json(200,{"ok":True,**state})
        if path=="/api/chat/archives": return self.send_json(200,{"ok":True,"chats":chat.archives() if chat else []})
        self.send_error(404)
    def do_POST(self):
        global manual_priority_until
        try: data=self.body()
        except (ValueError,json.JSONDecodeError): return self.send_json(400,{"ok":False,"error":"invalid JSON"})
        try:
            if self.path=="/api/drive":
                command=str(data.get("command","")); speed=max(.1,min(float(data.get("speed",.70)),1.))
                if command not in DRIVE_COMMANDS: raise ValueError("unknown drive command")
                manual_priority_until=time.monotonic()+DEADMAN_SECONDS; apply_drive(DRIVE_COMMANDS[command],speed,"manual",command); return self.send_json(200,{"ok":True})
            if self.path=="/api/vector":
                f,t,s=[max(-1.,min(float(data.get(k,0)),1.)) for k in ("forward","turn","strafe")]; speed=max(.1,min(float(data.get("speed",.70)),1.)); manual_priority_until=time.monotonic()+DEADMAN_SECONDS; apply_drive({"M2":f+t+s,"M3":f-t-s,"M1":f+t-s,"M4":f-t+s},speed,"manual","vector"); return self.send_json(200,{"ok":True})
            if self.path=="/api/agent/vector":
                if self.client_address[0] not in ("127.0.0.1","::1"): return self.send_json(403,{"ok":False,"error":"local access only"})
                if time.monotonic()<manual_priority_until: return self.send_json(409,{"ok":False,"error":"manual control has priority"})
                f,t,s=[max(-1.,min(float(data.get(k,0)),1.)) for k in ("forward","turn","strafe")]; speed=max(.5,min(float(data.get("speed",.70)),1.)); apply_drive({"M2":f+t+s,"M3":f-t-s,"M1":f+t-s,"M4":f-t+s},speed,"agent","vector"); return self.send_json(200,{"ok":True})
            if self.path=="/api/stop": stop_all(); return self.send_json(200,{"ok":True})
            if self.path=="/api/chat":
                if not chat: raise RuntimeError("agent disabled")
                chat.submit(str(data.get("message",""))); return self.send_json(202,{"ok":True})
            if self.path=="/api/chat/new":
                if not chat: raise RuntimeError("agent disabled")
                chat.new_chat(); return self.send_json(200,{"ok":True})
            if self.path=="/api/chat/stop": stop_all(); chat and chat.abort(); return self.send_json(200,{"ok":True})
        except (TypeError,ValueError) as exc: return self.send_json(400,{"ok":False,"error":str(exc)})
        except Exception as exc: return self.send_json(409,{"ok":False,"error":str(exc)})
        self.send_error(404)
    def serve_file(self,path,content_type):
        try: body=path.read_bytes()
        except FileNotFoundError: return self.send_error(404)
        self.send_response(200); self.send_header("Content-Type",content_type); self.send_header("Content-Length",str(len(body))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(body)
    def snapshot(self):
        frame=camera_output.frame
        if frame is None: return self.send_json(503,{"ok":False,"error":camera_error or "camera frame unavailable"})
        self.send_response(200); self.send_header("Content-Type","image/jpeg"); self.send_header("Content-Length",str(len(frame))); self.send_header("Cache-Control","no-store"); self.end_headers(); self.wfile.write(frame)
    def stream_camera(self):
        if camera_error: return self.send_json(503,{"ok":False,"error":camera_error})
        self.send_response(200); self.send_header("Cache-Control","no-cache, private"); self.send_header("Content-Type","multipart/x-mixed-replace; boundary=FRAME"); self.end_headers()
        try:
            while not shutdown_event.is_set():
                with camera_output.condition: camera_output.condition.wait(timeout=2); frame=camera_output.frame
                if frame: self.wfile.write(b"--FRAME\r\nContent-Type: image/jpeg\r\nContent-Length: "+str(len(frame)).encode()+b"\r\n\r\n"+frame+b"\r\n")
        except (BrokenPipeError,ConnectionResetError): pass
def watchdog():
    while not shutdown_event.wait(.1):
        now=time.monotonic()
        with motor_lock: expired=any(d and now>d for d in deadlines.values())
        if expired: stop_all()
        if not MOCK and (camera_error or (now-process_started_at>8 and camera_output.frame_count<2) or (camera_output.last_frame_at and now-camera_output.last_frame_at>5)):
            print(f"Camera watchdog stopping service: {camera_error or 'camera stalled'}",flush=True); stop_all(); shutdown_event.set()
def shutdown(*_args): shutdown_event.set(); stop_all()
if __name__=="__main__":
    signal.signal(signal.SIGTERM,shutdown); signal.signal(signal.SIGINT,shutdown); threading.Thread(target=watchdog,daemon=True).start(); start_camera()
    if os.getenv("PI_BOT_DISABLE_AGENT")!="1":
        try: chat=ChatManager(PROJECT_ROOT)
        except Exception as exc: print(f"Agent unavailable: {exc}",file=sys.stderr)
    server=ThreadingHTTPServer((HOST,PORT),Handler); server.timeout=.5; print(f"pi.bot listening on http://{HOST}:{PORT}",flush=True)
    try:
        while not shutdown_event.is_set(): server.handle_request()
    finally:
        shutdown(); server.server_close(); chat and chat.pi.stop()
        if camera is not None: camera.stop_recording(); camera.close()
        for motor in motors.values(): motor.close()
