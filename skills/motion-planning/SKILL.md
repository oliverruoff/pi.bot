---
name: motion-planning
description: Convert requested robot turns, distances, directions, and gestures into reasoned bounded Mecanum move calls using pi.bot geometry, empirical calibration, and camera verification. Use whenever a user requests physical movement, including approximate angles or distances.
---

# Motion planning

Turn a user's physical goal into an estimate–act–observe–correct loop. Do not substitute an arbitrary duration for reasoning.

## Known geometry

- Body footprint: approximately 30 × 30 cm.
- Wheel diameter: approximately 6 cm; circumference is `π × 6 ≈ 18.85 cm`.
- Mecanum wheel centers are approximately 15 cm forward/back and 15 cm left/right of the body center. For ideal in-place rotation, use the kinematic coefficient `Lx + Ly ≈ 30 cm`.
- Motors need at least 50% PWM to overcome static friction. Normal PWM is 70%.
- There are no wheel encoders. Geometry predicts wheel travel, not motor duration. Floor, battery, load, wheel slip, and roller geometry change the result.

For an in-place turn of `θ` radians, estimated travel magnitude at every wheel is:

```text
wheel_path_cm = (Lx + Ly) × |θ|
wheel_revolutions = wheel_path_cm / 18.85
```

Thus an ideal 90° turn (`θ = π/2`) needs about 47 cm of wheel travel, or roughly 2.5 wheel revolutions. This is a starting estimate, not proof of achieved heading.

For straight translation, estimated wheel revolutions are `distance_cm / 18.85`. Strafing has substantially more slip; rely more heavily on empirical calibration and shorter verification segments.

## Calibration

Read `data/motion-calibration.md` if it exists. Treat measurements matching the requested motion type and PWM as stronger evidence than theoretical estimates. The file may record values such as seconds per 90° turn, centimetres per second, surface, PWM, battery condition, sample count, and uncertainty.

If calibration is absent, do not pretend wheel travel determines time. At 70% PWM, begin with a short pulse appropriate to the available space, normally 0.25–0.5 seconds. Take a photo before and after, judge landmark displacement, then continue in smaller corrective pulses. Ask the user for observed angle/distance when camera evidence cannot quantify it. With the user's measurement, calculate a new duration proportionally and record a conservative calibration entry under `data/`.

Never alter a calibration from one ambiguous observation. Prefer several measurements, preserve surface/PWM context, and use a median or bounded range.

## Execute a request

1. Identify the target translation, rotation, or combined gesture and its tolerance. “Turn around” normally means about 180°; clarify genuinely ambiguous goals.
2. Check `get_robot_status` and `get_drive_status`. Take a photo when visual clearance or heading verification matters.
3. Calculate expected wheel travel from geometry and select duration from matching calibration. If timing is uncalibrated, explicitly treat the first short pulse as measurement.
4. Decompose the motion into bounded segments. Use pure vectors for measurable primitives:
   - right turn: `forward=0, turn=1, strafe=0`;
   - left turn: `forward=0, turn=-1, strafe=0`;
   - forward/back: only `forward` nonzero;
   - right/left strafe: only `strafe` nonzero.
5. Call `move`, which guarantees a final stop. Re-observe after every segment that could materially change clearance or heading.
6. Compare observed progress with the target. Correct with a shorter pulse or stop and ask the user if evidence is insufficient.
7. Report the achieved result as approximate unless externally measured. Never claim an exact angle or distance from timing alone.

## Safety bounds

- Manual input or emergency stop ends the plan immediately.
- Never compensate for a stalled robot by exceeding configured speed/duration caps.
- Stop on unexpected obstacles, conflicting camera evidence, wheel slip, or loss of localization.
- A monocular image cannot guarantee a collision-free turn; when clearance is uncertain, ask the user to clear space.

