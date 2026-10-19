# Smart-Home Automation Layer — Rule Registry & Design Principles

> **Note:** All resident names (`Alex`), pet names (`pet`), device names, and device IDs (`example-st-*`, `device@example-gh-*`) in this document are fictional placeholders.

Modeled on Home Assistant's core abstractions without requiring an always-on local LAN server.

## 1. Architecture (Home Assistant-Modeled)

Three core Home Assistant concepts, adapted to a cloud/CLI agent environment:

1. **Entity State Machine (`engine/entity_store.py`)**
   Writes `hidden_files/entity-states.json`: every entity has one canonical state (`binary_sensor.alex_home`, `media_player.smart_tv`, `purifier.kitchen`, `speaker.living`, `laundry.washer`, `light.*`, `sensor.*_humidity`, etc.). Automations read exclusively from this file—never directly from raw vendor APIs.
2. **Declarative Automations (`automations/*.yaml`)**
   Each rule is a `trigger` / `condition` / `action` triple evaluated by `engine/automation_engine.py`. Templates use `{{ python }}` expressions with `states{}`, `is_state()`, and `state_attr()`.
3. **Services**
   `winix.set_mode`, `notify.broadcast`, `light.turn_on/off`, `humidifier.turn_on/off`, `media_player.turn_off`, `media_player.play_pet_music`. Thin wrappers over verified control paths (direct vendor APIs, Google Home `AssistantBroadcast`, SmartThings API).

### Why Not a Local Home Assistant Server?
- **Speaker broadcasts** are the backbone of in-home notifications. Google Home's `AssistantBroadcast` trait via Home MCP provides reliable first-party TTS broadcasts without local Cast workarounds.
- **Zero local hardware maintenance:** When cloud/API paths (SmartThings API/MCP, direct purifier API, Google Home MCP, and phone webhooks) are reachable directly from the agent environment, a local box on the home LAN adds maintenance overhead without capability gain.

---

## 2. Core Principles

### One Automation Registry & Single Context Engine
- **Context Engine (`engine/home_context.py`):** Gathers zone, SmartThings location mode, sleep state, TV state, work state, who's-home (`alex`, `partner`, `pet`), and time bucket into a 5-minute cache. Consumed by `entity_store.py`, not by individual automations.
- **One Device Registry (`device-registry.example.json`):** Maps every device across platforms (`both_platforms`, `gh_exclusive`, `st_only`), records both SmartThings and Google Home IDs where applicable, and declares which platform is authoritative for state versus control.
- **Unknown-State Hardening:** A failed or absent sensor read must **never** look like a state transition. Both `entity_store.py` and `automation_engine.py` hold the last known timestamp/state when either the previous or current state is `unknown` or `None`.

### Mode Model (`home` / `away` / `night` / `morning`)
- **Single Mode Entry Point:** House modes (`home`, `away`, `night`, `morning`) flow through a single synchronization script (`mode-sync.sh`) that updates SmartThings location mode, writes the desired mode to the webhook worker, fires the universal phone macro (`?do=mode&m=<mode>`), and verifies by read-back.
- **Phone Mode Mapping:** SmartThings location modes (`Home`, `Away`, `Night`) and phone routines ("Switch Home mode", "Switch Away mode", "Switch Sleep mode", "Turn on Wake-up time") are kept in lockstep. Phone-only overlays (such as `theater` for movies) fire directly on the phone without altering the house mode.
- **Conflict Guard & Switch-Twice Rule:** Before and after changing phone modes, check active routines so conflicting latched routines are turned off via their own switch semantics rather than stacked over. If a switch-style routine was started by an on-phone schedule rather than the webhook, toggle it twice (on, then off) to clear the latch cleanly.

### Three-Source Presence Voting
Presence and occupancy combine three complementary signal tiers so no single flaky feed causes false triggers:
1. **Phone Geofence & Location State (`location-state.json`):** Primary home/away zone signal with an expiration window (stale after 240 minutes → `unknown`, never guessed as away).
2. **SmartThings Location Mode & Calendar Context:** `Home` / `Night` / `Away` mode state combined with active calendar blocks (sleep blocks, work/focus blocks) to distinguish "home and idle", "home and working", and "home and sleeping".
3. **Room Motion & Tracker Geofences:**
   - **Room Multisensors (`sensor.*` / `binary_sensor.*_motion`):** Motion is treated as a *recency signal*, not true continuous occupancy. Recent active motion in a single room pins the resident's current room for targeted broadcasts.
   - **SmartTag Trackers (`stfind`):** Passive location reads for essentials and pet collar geofencing (`<250m` from home coordinates within 120 minutes). If the pet collar battery dies or a read goes stale, fail safe by assuming the pet is home (`assumed-home`).

### Consolidated Broadcast & Spoken-Alert Routing
All notifications follow strict routing and privacy guardrails:
1. **When Home & Awake → Google Home `AssistantBroadcast`:**
   - Never use phone TTS alone or silent chat alone when the resident is home; always accompany speaker broadcasts with a chat log record.
   - **Room targeting:** Check motion across the room multisensors; if recent motion pins a single room, broadcast only to that room's speaker. Otherwise, fire a single structure-wide broadcast (never loop speaker-by-speaker sequentially, which causes overlapping audio echo).
   - **Sleep gate:** Never broadcast when `binary_sensor.sleeping` is `on`.
2. **When Away (and Not at Work) → Phone Webhook Speak (`?do=speak`):**
   - Reserved for urgent, time-sensitive act-now alerts (max ~2/day, ≤130 characters, never during quiet hours 10 PM–7 AM or active meetings).
   - **At-Work Hard Suppression:** Never fire phone TTS when the resident is at work (detected via office zone, Work mode, or active work/meeting calendar blocks). When work state is uncertain during weekday work hours, fail closed (silent notification only).
3. **Sanitization:** Never include medication names, dosages, financial figures, or personal identifiers in any speaker broadcast or spoken phone announcement.

### Verify-After-Write & Actor Census Discipline
- **Verify-After-Write:** Every state-changing command (SmartThings switch/mode change, Winix mode/fan command, MacroDroid mode sync) must be verified by reading back live state or webhook ack (`in_sync`) rather than assuming fire-and-forget success. Prefer SmartThings state over Google Home MCP state for shared devices because Google Home MCP state can lag.
- **Actor Census Before Pausing:** When an unwanted device change occurs, census every surface that can act on that device (SmartThings Rules, Google Home routines, hooks, cron jobs, the declarative automation engine, and phone routines) and match by device, trigger, and firing interval before pausing or deleting anything. Prefer pausing over deleting so changes are reversible.

---

## 3. Programmatic Execution & Model Routing

### Hook-Driven Zero-Token Polling
- No LLM runs steady-state automations. The `automation_engine_poll.sh` hook runs `engine/automation_engine.py --hook` every 10 minutes.
- Python performs 100% of entity refresh, trigger/condition evaluation, and service execution.
- The agent wakes **only** when `ERROR_COUNT > 0` (`automation-engine-error`) or `BROADCAST_COUNT > 0` (`automation-broadcast-sent`, to post the companion chat transcript). Quiet polls consume zero model tokens.

### Model Routing Policy
- **Steady state:** Pure Python (zero tokens).
- **Judgment moments & real-world side effects:** Primary reasoning model (engine failure diagnosis, broadcast chat records, multi-agent deliberations).
- **Bulk drafting/migration:** Fast/low-cost models for initial YAML or script drafts, verified by the primary model before deployment.
- **Privacy filter:** Presence patterns, sleep state, and location coordinates are household-sensitive; redact or keep them on trusted primary routes rather than sending raw personal telemetry to third-party APIs.

---

## 4. Example Automations (`automations/`)

1. **Air Quality Semantic Control (`automations/air-quality.yaml`)**
   - Runs every 10 minutes. Sets purifiers (`purifier.kitchen`, `purifier.bedroom`) to `auto` when sleeping, watching TV (`media_player.smart_tv`), or working (`binary_sensor.working`), and `turbo` when away or idle. Keeps PlasmaWave always on.
   - Note: In the Winix API, `"turbo"` is a fan speed (`A04`), not a mode attribute (`A03` supports `auto`/`manual`). The engine maps semantic `turbo` to `mode=manual` + `fan=turbo`.
2. **Laundry Finished (`automations/laundry.yaml`)**
   - Watches Google Home-exclusive `laundry.washer` and `laundry.dryer` entities for state transition `to: 'off'`.
   - Gated on `binary_sensor.alex_home == 'on'` and `binary_sensor.sleeping == 'off'`. Broadcasts completion over `speaker.living` and `speaker.kitchen`.
3. **Pet Comfort (`automations/pet.yaml`)**
   - When `binary_sensor.alex_home` transitions to `'off'` and `binary_sensor.pet_home` is `'on'`, powers on `media_player.streaming_tv` via Google Home and launches gentle pet comfort music on YouTube via `AssistantFulfillment.OkGoogle`. Turns the TV off when `binary_sensor.alex_home` returns to `'on'`.
