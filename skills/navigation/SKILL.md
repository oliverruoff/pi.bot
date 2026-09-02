---
name: navigation
description: Localize and navigate Navibot in a known home using current camera observations and the human-editable text map. Use for destinations, room localization, route planning, and guided map creation; not for precise SLAM or guaranteed obstacle avoidance.
---

# Semantic navigation

Use only `take_photo`, `move`, `stop`, `get_drive_status`, and `get_robot_status` for physical navigation. The Markdown under `map/` is the only runtime map; never search for or depend on reference photos.

## Navigate

1. Read `map/floorplan.md` and only the room files relevant to the current hypotheses or destination.
2. Resolve the requested point of interest and its room. If it is absent or ambiguous, ask the user rather than inventing it.
3. Call `take_photo`, factually describe stable visible features, and compare them with the map.
4. State room, supported sub-location, likely heading, confidence, evidence, and competing hypotheses. One image rarely establishes an exact pose.
5. Select one visible room transition or landmark. Execute only a short, low-speed, reversible `move`; then stop, take another photo, and localize again.
6. Repeat perceive–plan–act–verify. Stop immediately if the expected landmark disappears, observations conflict with the map, a possible obstacle appears, localization is insufficient, or a tool reports manual override/error.

Prefer room-to-room transitions over long open-loop paths. Never imply centimetre accuracy or obstacle-avoidance capability. Turning in place is the preferred observation action; keep it bounded and re-observe.

## Report

Before movement, provide a compact result in this shape:

```text
location:
  room: <room or unknown>
  sub_location: <supported location or unknown>
  heading: <likely direction or unknown>
  confidence: <low|medium|high>
  evidence: <stable matching features>
  next_observation: <needed action, or none>
```

Describe the next action briefly, for example: “Turn slightly right toward the doorway, stop, and take another photo.” Physical movement still requires the bounded tools.

## Build or update the map

When the user asks to map the home, capture multiple current views of each room, including doors, corners, large fixed furniture, and named destinations. Draft or update the appropriate Markdown file using the schema in `map/rooms/_room-template.md`, then update connections and naming in `map/floorplan.md`. Clearly mark unverified facts. Favor stable large features; exclude temporary objects and lighting-dependent details. Never overwrite a human correction merely because one observation differs.

