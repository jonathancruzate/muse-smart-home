# Scrub Report — Smart-Home Public Edition (`dist/`)

This report documents every category of private/instance data found across the private input files (`~/workspace/smart-home-public/work/` and the skill/connector source trees) and how each category was scrubbed, replaced with clearly fictional placeholders, generalized, or dropped in `~/workspace/smart-home-public/dist/`.

> **Verifier Note:** No original private strings, names, coordinates, or UUIDs are repeated verbatim in this file either, so a full-tree grep across `dist/` (including `SCRUB-REPORT.md`) returns zero matches for any private identifier.

---

## 1. Scrub Categories Summary

| Category | Found in Private Inputs | Action Taken in `dist/` |
|---|---|---|
| **People's Names & Assistant Persona** | Owner's first and last name, partner's first name, dog walker's name in automation comments, household contacts, and personal assistant persona name | Replaced primary resident with `Alex` (`binary_sensor.alex_home`, `owner_key: "alex"`), partner with `partner`, assistant persona with `the agent` / `Muse agent`, and dropped dog-walker references. |
| **Pet Name & Pet Automation Identifiers** | Dog's name in YAML filename, entity IDs, collar state file, device registry notes, and TV music service | Renamed file to `automations/pet.yaml`; replaced with `pet`, `binary_sensor.pet_home`, `pet-collar.json`, `"Pet Collar"`, `pet_comfort_music_on/off`, and `media_player.play_pet_music`. |
| **Home Coordinates, Building & Employer** | Home latitude/longitude constants in `home_context.py`, references to `"apartment"`, workplace/employer neighborhood zone name, and CDN sign-in country notes | Replaced coordinates with `0.0, 0.0` (`pet_home_lat: 0.0`, `pet_home_lng: 0.0`); replaced `"apartment"` with `"home"`; replaced employer office name with generic `"office location zone"`; dropped personal sign-in geolocation anecdotes. |
| **Personal Domain, Webhook URLs & Secrets** | Personal domain hosting the Cloudflare Worker (`/st-webhook/ingress`, `/st-webhook/mode-ack`, `/st-webhook/mode-desired`, `/st-webhook/mode-state`) and webhook capability URL references | Replaced with `https://your-worker.example.com/st-webhook/...`, secret query parameter `<your-secret>`, and `https://trigger.macrodroid.com/example-device-id/muse`. |
| **SmartThings Location & Device UUIDs** | 1 home location UUID, 13 `both_platforms` device UUIDs, 4 retired virtual-switch UUIDs, and 9 SmartTag tracker UUIDs | Replaced all device and location UUIDs with fictional placeholders (`example-st-location-001`, `example-st-sensor-001..003`, `example-st-camera-001`, `example-st-light-001..008`, `example-st-tv-001`, `example-st-collar-001`). Dropped the 9-tag personal tracker inventory table and retired virtual-switch UUIDs. |
| **Google Home Device IDs** | 23 `device@<uuid>` strings across `device-registry.json` and `gh_devices.py` | Replaced all 23 IDs with `device@example-gh-sensor-001..003`, `device@example-gh-camera-001`, `device@example-gh-light-001..008`, `device@example-gh-tv-001`, `device@example-gh-mop-001`, `device@example-gh-dryer-001`, `device@example-gh-humidifier-001`, `device@example-gh-speaker-001..005`, `device@example-gh-washer-001`, and `device@example-gh-streamtv-001`. |
| **Winix Purifier Hardware IDs** | 2 hardware MAC+suffix identifiers in `automation_engine.py`, `entity_store.py`, and `winix_controller.py` | Replaced with `example-winix-device-001` and `example-winix-device-002`. |
| **Phone, Watch & Appliance Model/Serial Specifics** | 16-char hex Android device ID, specific phone model string, smartwatch Bluetooth name/suffix, washer/dryer 10-digit model numbers, and household-specific device labels | Dropped Android hex ID, watch Bluetooth suffix, and washer/dryer model numbers. Generalized household-specific labels (`hallway_light`, `smart_tv`, `robot_mop` / `"Robot Mop"`, `streaming_tv` / `"Living Room Streaming TV"`). |
| **Personal Event Dates, Schedules & Incident Logs** | Specific October dates/timestamps across comments and docs, speaker outage timestamp, 4 AM washer completion anecdote, dog-walker weekday/time window, in-office weekday list, sprint tags, and support ticket IDs | Removed all personal event dates, outage timestamps, weekday schedules, and ticket/sprint IDs while preserving the underlying engineering rules. |

---

## 2. File-by-File Actions (`dist/`)

### `dist/README.md`
- Documented system purpose, architecture diagram, directory structure, `config.example.json` + environment variable configuration, and included a prominent notice that all names, devices, coordinates, and IDs are fictional placeholders.

### `dist/device-registry.example.json` (from `work/device-registry.json`)
- **Replaced:** All 13 `both_platforms` SmartThings UUIDs and Google Home `device@<uuid>` strings replaced with `example-st-*` and `device@example-gh-*`.
- **Replaced:** All 10 `gh_exclusive` Google Home `device@<uuid>` strings replaced with `device@example-gh-*`.
- **Generalized:** Renamed household-specific keys to generic equivalents (`hallway_light`, `smart_tv`, `robot_mop`, `streaming_tv`) and stripped appliance model numbers.
- **Dropped:** Personal speaker outage timestamp on `speaker_living` and personal item list in `st_only._note`.

### `dist/automations/pet.yaml` (renamed from `work/automations/` pet automation)
- **Replaced:** Renamed aliases to `pet_comfort_music_on` and `pet_comfort_music_off`; replaced resident and pet entities with `binary_sensor.alex_home` and `binary_sensor.pet_home`; replaced service and target with `media_player.play_pet_music` and `media_player.streaming_tv`.
- **Dropped:** Comment referencing the dog walker's name and Tue/Thu/Fri schedule window.

### `dist/automations/laundry.yaml` (from `work/automations/laundry.yaml`)
- **Replaced:** Resident presence entity replaced with `binary_sensor.alex_home`.
- **Dropped:** Personal anecdote about a 4 AM overnight washer run and date stamp, preserving the sleep-gate rule.

### `dist/automations/air-quality.yaml` (renamed from `work/automations/` purifier automation)
- **Replaced:** Alias renamed to `air_quality_semantic_control`; TV entity replaced with `media_player.smart_tv`.
- **Dropped:** Migration date stamp in comment. Kept 10-minute interval and template logic intact.

### `dist/engine/config.example.json` (new centralized config file)
- Created to hold all configurable device IDs, entity names, coordinates (`0.0, 0.0`), timezone (`America/New_York`), and CLI paths using `example-*` placeholders.

### `dist/engine/automation_engine.py` (from `work/bin/automation_engine.py`)
- **Generalized:** Added `load_config()` reading `engine/config.json` (or `SMART_HOME_CONFIG` / `config.example.json`) plus environment variable overrides.
- **Replaced:** Hardcoded purifier IDs, 6 lamp UUIDs, TV UUID, streaming TV name, and speaker names replaced with config-driven maps defaulting to `example-*` placeholders. Renamed pet music service to `media_player.play_pet_music` (`svc_media_player_play_pet_music`).

### `dist/engine/entity_store.py` (from `work/bin/entity_store.py`)
- **Generalized:** Added `load_config()` and environment variable support.
- **Replaced:** Hardcoded sensor UUIDs, lamp UUIDs, and purifier IDs replaced with config-loaded dictionaries using `example-*` placeholders. Resident and pet entity names parameterized via `OWNER_KEY` (`alex`) and `PET_KEY` (`pet`).

### `dist/engine/gh_devices.py` (from `work/bin/gh_devices.py`)
- **Generalized:** Added `load_config()` and environment variable support.
- **Replaced:** All 10 hardcoded `device@<uuid>` strings in `EXCLUSIVE` replaced with config-loaded `device@example-gh-*` placeholders.

### `dist/engine/home_context.py` (from `work/bin/home_context.py`)
- **Generalized:** Added `load_config()` and environment variable overrides (`SMART_HOME_TIMEZONE`, `SMART_HOME_LOCATION_ID`, `SMART_HOME_TV_ST_ID`, `SMART_HOME_PET_LAT`, `SMART_HOME_PET_LNG`, `SMART_HOME_PET_COLLAR_ID`).
- **Replaced:** Location UUID, TV UUID, pet collar UUID, and home lat/lng coordinates replaced with `example-st-*` and `0.0, 0.0`. `whos_home` keys replaced with `alex`, `partner`, and `pet`; collar state file renamed to `pet-collar.json`.
- **Dropped:** Personal comments and date stamps.

### `dist/engine/winix_controller.py` (from `work/bin/winix_controller.py`)
- **Generalized:** Added `load_config()` and environment variable support; ensured `turbo` sets `mode=manual` before `fan=turbo`.
- **Replaced:** Hardcoded purifier hardware IDs replaced with `example-winix-device-001` and `example-winix-device-002`; removed owner's name from docstring.

### `dist/engine/automation_engine_poll.sh` (from `work/hooks/automation_engine_poll.sh`)
- **Generalized:** Made `ENGINE` path configurable via `${SMART_HOME_ENGINE:-...}`.

### `dist/rules/automation-rules.md` (from `work/automation-rules.md` + skill sources)
- **Generalized:** Distilled architectural principles (Home Assistant entity/automation/service model, single context engine, mode model `home`/`away`/`night`/`morning`, three-source presence voting, broadcast and spoken-alert routing, verify-after-write, actor census, and hook-driven zero-token polling).
- **Dropped:** Personal event dates, internal sprint tags, VM-specific provider paths, and device outage timestamps.

### `dist/skills/googlehome/SKILL.md` (from `custom-skills/local-googlehome/SKILL.md`)
- **Replaced:** Removed owner's name and assistant persona name.
- **Dropped:** Owner-specific cloud project setup date/name and internal `/opt/muse/skills/...` path references.

### `dist/skills/macrodroid/SKILL.md` & `dist/skills/macrodroid/schema-essentials.md` (from `custom-skills/local-macrodroid/` & `custom-skills/local-phone-automations/`)
- **Replaced:** Replaced personal worker domain with `https://your-worker.example.com/st-webhook/...` and `<your-secret>`; replaced phone model/owner references with generic placeholders.
- **Extracted:** Universal webhook macro pattern (`?do=mode|speak|status`), Mode Poll backstop, Notification Push, Low Battery Alert, mode-sync routine conflict & switch-twice rules, spoken alert guardrails (home override, at-work suppression, quiet hours, ≤130 chars, sanitization), on-demand status polling, and MacroDroid 6.0 JSON schema essentials.
- **Dropped:**
  - Owner's retired Bixby keyword registry and retired 19-field/6-field Bixby status dump tables (personal historical records).
  - Personal debugging anecdotes, notification shade screenshot narratives, TeamViewer repair session notes, and `adb-tailscale` archive notes.
  - Owner-specific SmartThings connector incident logs, Samsung account sign-in notes, and personal 9-SmartTag inventory table from `CONNECTOR.md`.
