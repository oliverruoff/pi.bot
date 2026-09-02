import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const base = process.env.HARDWARE_BASE_URL || "http://127.0.0.1:8080";
const maxSpeed = Number(process.env.ROBOT_MAX_SPEED || "0.55");
const defaultSpeed = Number(process.env.ROBOT_DEFAULT_SPEED || "0.70");
const maxDuration = Number(process.env.ROBOT_MAX_DURATION || "5");

async function json(path: string, init?: RequestInit) {
  const response = await fetch(base + path, { signal: init?.signal, ...init });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Hardware HTTP ${response.status}`);
  return payload;
}
async function post(path: string, body: object, signal?: AbortSignal) {
  return json(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "move", label: "Move robot",
    description: "Move the Mecanum robot using a bounded vector for a finite duration. Positive forward is ahead, positive turn is right, and positive strafe is right.",
    parameters: Type.Object({
      forward: Type.Number({ minimum: -1, maximum: 1 }),
      turn: Type.Number({ minimum: -1, maximum: 1 }),
      strafe: Type.Number({ minimum: -1, maximum: 1 }),
      speed: Type.Optional(Type.Number({ minimum: 0.5, maximum: maxSpeed, description: "PWM power; defaults to 70%. Values below 50% cannot move this robot." })),
      duration: Type.Number({ minimum: 0.1, maximum: maxDuration }),
    }),
    async execute(_id, params, signal) {
      const body = { forward: params.forward, turn: params.turn, strafe: params.strafe, speed: Math.min(params.speed ?? defaultSpeed, maxSpeed) };
      const deadline = Date.now() + Math.min(params.duration, maxDuration) * 1000;
      try {
        do { await post("/api/agent/vector", body, signal); await new Promise((resolve, reject) => {
          const timer = setTimeout(resolve, Math.min(300, Math.max(0, deadline - Date.now())));
          signal?.addEventListener("abort", () => { clearTimeout(timer); reject(new Error("move aborted")); }, { once: true });
        }); } while (Date.now() < deadline);
        return { content: [{ type: "text", text: "Bounded movement completed and robot stopped." }] };
      } finally { try { await post("/api/stop", {}); } catch {} }
    },
  });
  pi.registerTool({ name: "stop", label: "Stop robot", description: "Immediately stop all robot motion.", parameters: Type.Object({}),
    async execute() { await post("/api/stop", {}); return { content: [{ type: "text", text: "Robot stopped." }] }; } });
  pi.registerTool({ name: "get_drive_status", label: "Drive status", description: "Get active drive source and safety deadline.", parameters: Type.Object({}),
    async execute() { const value=await json("/api/drive/status"); return { content: [{ type: "text", text: JSON.stringify(value) }] }; } });
  pi.registerTool({ name: "get_robot_status", label: "Robot status", description: "Get robot camera, uptime, and drive health.", parameters: Type.Object({}),
    async execute() { const value=await json("/api/status"); return { content: [{ type: "text", text: JSON.stringify(value) }] }; } });
  pi.registerTool({ name: "take_photo", label: "Take photo", description: "Capture the latest atomic camera frame for visual inspection.", parameters: Type.Object({}),
    async execute() { const response=await fetch(base+"/api/snapshot"); if(!response.ok) throw new Error(`Snapshot HTTP ${response.status}`); const data=Buffer.from(await response.arrayBuffer()).toString("base64"); return { content: [{ type: "image", data, mimeType: "image/jpeg" }] }; } });
}
