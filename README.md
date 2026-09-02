# Navibot

Navibot is a phone-controlled Raspberry Pi home robot with four Mecanum wheels, a Picamera2 live stream, persistent web chat, Pi Coding Agent integration, bounded robot tools, and a text-map navigation skill.

## Architecture

- `webserver/app.py` is the sole owner of GPIO, motors, and camera access. It serves the cockpit and a narrow local hardware API.
- `webserver/agent_bridge.py` manages Pi RPC, chat history, new sessions, progress, and configurable context compaction.
- `extensions/robot-tools.ts` exposes `move`, `stop`, `get_drive_status`, `take_photo`, and `get_robot_status` to Pi.
- `skills/navigation/` contains semantic navigation instructions and the human-editable Markdown map.
- `AGENTS.md` defines the robot identity and physical safety context.

Secrets and runtime state are deliberately excluded from Git. `.env.example` documents every required setting; `.env` and `data/` remain local to each robot.

## Agent setup guide

An installation agent should inspect `wiring.md`, `.env.example`, and the files under `docs/` before changing the robot. Do not guess missing hardware, login, network, or provider details. Ask the user when any of these are unknown:

- robot hostname/IP address and SSH username;
- whether Raspberry Pi OS is 64-bit (`uname -m` should report `aarch64` for automatic Node installation);
- whether the motor pin mapping and reversed flags in `webserver/app.py` match the physical robot;
- AI provider, model, and corresponding API key;
- desired effective PWM range when the motors do not overcome static friction;
- whether the robot is safely raised or has clear floor space before movement tests.

Never commit `.env`, print its API keys, or copy secrets into documentation or shell logs. Never run movement tests without ensuring that a stop command is sent afterward.

### 1. Prepare the Raspberry Pi

Required hardware/software assumptions:

- Raspberry Pi OS with Python 3, Picamera2/libcamera, `gpiozero`, Git, curl, and xz support;
- the service user belongs to `gpio`, `video`, and `render` groups;
- camera and the motor driver are wired as documented in `wiring.md`;
- TCP port 8080 is reachable only from a trusted network.

Install missing base packages when necessary:

```bash
sudo apt-get update
sudo apt-get install -y git curl xz-utils python3-gpiozero python3-picamera2
sudo usermod -aG gpio,video,render "$USER"
```

Log out and back in after changing groups.

### 2. Clone and configure

```bash
git clone https://github.com/oliverruoff/Navibot.git ~/Navibot
cd ~/Navibot
cp .env.example .env
chmod 600 .env
```

Edit `.env` locally on the robot. At minimum, choose `PI_ARGS` and provide the matching provider key. Example without a real secret:

```dotenv
PI_ARGS=--provider minimax --model MiniMax-M3
MINIMAX_API_KEY=replace-me
```

The default configuration allows 10 minutes for one agent turn and uses 70% motor PWM, with a 50% effective minimum and an 85% agent cap. Adapt these values to the hardware rather than editing source code.

### 3. Deploy

```bash
cd ~/Navibot
./deploy.sh
```

The script updates the selected Git branch, installs pinned Node 22.19 and Pi Coding Agent 0.84.4 versions when needed, adapts the systemd unit to the current checkout/user, enables the service, restarts it, and checks `/api/status`.

Supported overrides include:

```bash
APP_DIR=/home/bot/Navibot BRANCH=main SERVICE_USER=bot ./deploy.sh
```

`NODE_VERSION` and `PI_VERSION` can deliberately override the pinned runtime versions after compatibility testing.

### 4. Verify safely

First perform checks that cannot move the robot:

```bash
systemctl is-active servicebot-control
curl -fsS http://127.0.0.1:8080/api/status
curl -fsS http://127.0.0.1:8080/api/snapshot -o /tmp/navibot-snapshot.jpg
```

Open `http://ROBOT_IP:8080`, verify the camera, and send a chat prompt that explicitly forbids movement. Confirm `get_robot_status` and `take_photo` before testing `move`.

For a physical drive test, first ensure clear space or lift the wheels. Use a short pulse and always stop:

```bash
curl -fsS -X POST -H 'Content-Type: application/json' \
  --data '{"command":"forward","speed":0.7}' http://127.0.0.1:8080/api/drive
sleep 0.25
curl -fsS -X POST -H 'Content-Type: application/json' \
  --data '{}' http://127.0.0.1:8080/api/stop
curl -fsS http://127.0.0.1:8080/api/drive/status
```

The final drive status must report `active: false`. Also verify forward, backward, rotation, and strafing individually if the physical setup is safe.

## Navigation map

The checked-in map is intentionally empty because room facts are installation-specific. Ask Navibot to help map the home, then review the generated Markdown under `skills/navigation/map/`. Commit map files only if the user wants the home layout versioned; treat potentially sensitive household details accordingly.

## Development checks

```bash
python3 -m pip install -r requirements-dev.txt
python3 -m pytest -q
python3 -m py_compile webserver/app.py webserver/agent_bridge.py
```

To roll back, check out a known Git revision and rerun `./deploy.sh`. The prior `/home/bot/servicebot-control` installation is not modified or deleted by this repository.
