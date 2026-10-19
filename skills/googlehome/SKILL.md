# Google Home (MCP) Skill

> **Note:** All names and device identifiers in this document are generic placeholders. Adapt paths and device names to match your own home configuration.

- Control Google Home devices directly through Home MCP (`https://developers.home.google.com/mcp/home`).
- Treat SmartThings as the authoritative state source through its API/MCP, and Google Home as a control and broadcast surface through MCP (plus state reader for Google Home-exclusive appliances).

## Tooling

- Use the `gh` CLI wrapper (`~/workspace/skills/googlehome/bin/gh` or configured via `SMART_HOME_GH_CLI`) for `auth`, `homes`, `devices`, `state`, `history`, and `action`.
- Run `gh auth` only for the one-time OAuth loopback flow and keep the OAuth client and tokens in protected config files (`0600`).
- Run `gh devices` and `gh state` before acting when the exact device, its traits, or its current state matter.
- Run `gh history` when recent device events could explain a reported state change.
- Creating automations through MCP is not supported; create or change automations in the declarative automation engine (`automations/*.yaml`) or the platform that owns the device.

## Setup and Implementation Notes

- Enable the Home API in your Google Cloud project and complete the OAuth loopback grant before invoking `gh` commands.
- Send `run_home_actions` requests with the structure ID, device ID, device type, trait command, and parameters.
- Verify commands through SmartThings state when precision matters, because Google Home MCP state can lag behind SmartThings on shared devices.
- Third-party cloud links to Google Home (such as air purifiers or humidifiers) can occasionally drop; use the vendor's direct API path as the authoritative control/state surface when available.

## Operating Rules

- Request only the home platform OAuth scope required by the CLI and never broaden it.
- Never print, log, or expose access tokens or refresh tokens.
- Respect Google Home rate limits and avoid aggressive polling (use the 5-minute snapshot cache in `engine/gh_devices.py`).
- Never attempt to bypass server-side blocks on sensitive actions (such as unlocking doors or disarming security systems).

## Speaker Broadcasts

- Fire in-home spoken notifications through the `AssistantBroadcast` trait when the resident is home and awake—never through phone TTS alone and never as chat-only, while still posting the chat message as the written record.
- Check the location state file (`location-state.json` / `binary_sensor.alex_home`) and sleep state (`binary_sensor.sleeping`) first; broadcast only when the resident is confirmed home on fresh evidence and awake.
- Read motion across the room multisensors via SmartThings and broadcast to the single room speaker when recent motion pins the resident to one room.
- Fire one structure-wide broadcast when no sensor pins a single room. Never send the same message to multiple speakers sequentially one at a time, as sequential playback overlaps and echoes across rooms.
- Check online status via `engine/gh_devices.py` before targeting an individual speaker; devices absent from the `gh state` snapshot are offline.
- Read the local broadcast/automation log first when diagnosing an unexpected broadcast, because `gh history` does not record `AssistantBroadcast` commands.
- Keep broadcast text concise and sanitized: never include medication names, dosages, financial figures, or personal identifiers.

## Census Every Actor Before Pausing a Device Automation

- List every mechanism that can change a target device before pausing, disabling, or deleting anything when troubleshooting unwanted device behavior.
- Search across SmartThings Rules, Google Home routines, background hooks, cron jobs, the declarative automation engine (`automations/*.yaml`), and phone routines—the same device can have actors on multiple surfaces.
- Match each candidate actor by the target device, trigger condition, and firing interval rather than by a similar name or whichever surface is easiest to inspect.
- Pause only the actor whose trigger and interval match the observed behavior, leaving non-matching actors untouched.
- Record the identified culprit, the supporting evidence, and every other surface checked before changing any state.
- Verify the pause by observing the device remain in the desired state for longer than the culprit's firing interval. Treat a fresh unwanted change after a pause/deletion as proof that the true culprit is still active elsewhere.
- Restore any wrongly paused actor immediately once subsequent evidence shows it was not the culprit, keeping its saved specification so restoration requires no rebuild.
- Prefer pausing over deleting when two candidate actors look similar, because pausing is cleanly reversible.
- Prefer a live state read over a static rule list whenever the two disagree about whether an actor is still firing.
