# MacroDroid Universal-Macro & Phone Webhook Pattern

> **Note:** All URLs (`https://your-worker.example.com/...`, `https://trigger.macrodroid.com/example-device-id/...`), secrets (`<your-secret>`), and routine names in this document are placeholders. Do not embed real secrets in documentation or commit them to version control.

This skill documents the **Universal Macro** pattern for bidirectional phone control and telemetry between a Muse agent (via a Cloudflare Worker or webhook relay) and an Android phone running **MacroDroid 6.0+**.

---

## 1. Architecture: Two Channels

1. **Channel 1 — Universal Webhook Macro (Agent → Phone)**
   Instead of maintaining dozens of separate webhook macros or fragile notification-keyword triggers, a **single** MacroDroid macro (`WebHookTrigger` with identifier `muse`) receives all inbound commands at:
   `https://trigger.macrodroid.com/example-device-id/muse`
   Query parameters (`?do=<control>&m=<option>`) are saved into a local dictionary variable (`qp`) and routed via `IfConditionAction` / `ElseIfConditionAction` branches.

2. **Channel 2 — Worker Ingress & Poll Backstop (Phone → Agent)**
   MacroDroid macros push phone notifications, on-demand status, mode acknowledgments, and low-battery alerts to your Worker ingress endpoint:
   `https://your-worker.example.com/st-webhook/...`

---

## 2. The Universal Macro Design (`?do=...`)

The universal macro declares:
- **`WebHookTrigger` (`identifier: "muse"`):**
  - `queryParamsDictionaryName: "qp"` (local dictionary, `m_type: 4`)
  - `saveBodyVariableName: "req_body"` (local string, `m_type: 2`)
- **Top-level branches on `{lv=qp[do]}`:**

| Request (on the `muse` webhook URL) | Branch Condition | Effect on Phone | Worker Ack / Output |
|---|---|---|---|
| `?do=mode&m=night` (GET, via `mode-sync`) | `{lv=qp[do]} == "mode"` and `{lv=qp[m]} == "night"` | Invokes the phone's Sleep mode routine step | POSTs `night` to `https://your-worker.example.com/st-webhook/mode-ack?secret=<your-secret>` + shared ack |
| `?do=mode&m=home` (GET, via `mode-sync`) | `{lv=qp[do]} == "mode"` and `{lv=qp[m]} == "home"` | Invokes the phone's Home mode routine step | POSTs `home` to `/st-webhook/mode-ack` + shared ack |
| `?do=mode&m=morning` (GET, via `mode-sync`) | `{lv=qp[do]} == "mode"` and `{lv=qp[m]} == "morning"` | Invokes the phone's Wake-up routine step | POSTs `morning` to `/st-webhook/mode-ack` + shared ack |
| `?do=mode&m=away` (GET, via `mode-sync`) | `{lv=qp[do]} == "mode"` and `{lv=qp[m]} == "away"` | Invokes the phone's Away mode routine step | POSTs `away` to `/st-webhook/mode-ack` + shared ack |
| `?do=mode&m=theater` (GET, direct) | `{lv=qp[do]} == "mode"` and `{lv=qp[m]} == "theater"` | Invokes the phone-only Theater overlay routine step | Shared ack only (does not alter house mode) |
| `?do=speak` (POST, `text/plain` body) | `{lv=qp[do]} == "speak"` | `SpeakTextAction` speaks `{lv=req_body}` aloud via TTS | Shared ack |
| `?do=status` (GET) | `{lv=qp[do]} == "status"` | POSTs 5-field device status (`battery`, `charging`, `wifi_ssid`, `ringer_mode`, `screen_on`) to `https://your-worker.example.com/st-webhook/ingress?secret=<your-secret>` | Shared ack |

### Companion Phone Macros (Phone → Worker)
Alongside the universal webhook macro, three lightweight background macros complete the contract:
1. **Mode Poll Backstop (`RegularIntervalTrigger`, every 300s):**
   - `GET https://your-worker.example.com/st-webhook/mode-desired?secret=<your-secret>` (returns JSON `{"mode": "...", "seq": N, "updated_at": ...}`).
   - `JsonParseAction` parses the response string into a local dictionary, extracts `mode`, and compares against global `muse_last_mode`.
   - Only when `mode != {v=muse_last_mode}`: updates `muse_last_mode`, runs the corresponding mode routine step, and POSTs the mode ack to `/st-webhook/mode-ack`. This ensures a missed webhook self-heals within 5 minutes while avoiding unnecessary Worker KV writes.
2. **Notification Push (`NotificationTrigger`, any app):**
   - `POST https://your-worker.example.com/st-webhook/ingress?secret=<your-secret>` with `text/plain` lines:
     `phone-notify` / `{not_app_package}` / `{not_title}` / `{notification}`.
3. **Low Battery Alert (`BatteryLevelTrigger`, `m_decreasesTo: true`, `m_batteryLevel: 20`):**
   - Sends the same 5-field `phone-status` POST to `/st-webhook/ingress` when battery drops to 20%.

---

## 3. Phone Control & Spoken Alert Rules

### House Mode Synchronization
- **Single Writer (`mode-sync.sh`):** House modes (`home`, `away`, `night`, `morning`) must always go through the mode-sync layer, never raw ad-hoc webhook calls. The script writes the desired mode to the Worker, fires `?do=mode&m=<mode>`, and verifies via `GET https://your-worker.example.com/st-webhook/mode-state` (`in_sync` when actual matches desired).
- **Routine Conflict Check & Switch Semantics:**
  - Before and after changing modes, verify no conflicting routine remains latched on the phone.
  - Switch-type routines must be exited using their own trigger rather than stacking another mode on top.
  - **Switch-Twice Rule:** If a switch-class mode (such as Sleep mode) was started by an on-phone schedule rather than the webhook, fire that mode's trigger **twice** (on, then off) to clear the latch cleanly.
  - **Skip-Fire Rule:** If a live routine census shows the target switch routine is already running, do not re-fire its toggle (which would turn it off).

### Spoken Announcements (`?do=speak`)
- **Home Override:** When the resident is home and awake, **never** use phone TTS—use Google Home `AssistantBroadcast` instead. Phone speak is strictly for away-from-home urgent alerts (or when home speakers are unreachable).
- **At-Work Hard Suppression:** Never fire `?do=speak` when the resident is at work (office location zone, Work mode, or active work/meeting calendar block). When work state is uncertain during weekday work hours, fail closed (silent notification only).
- **Guardrails:**
  - Maximum ~2 spoken alerts per day (deduplicated per situation).
  - Hard block during quiet hours (10 PM–7 AM) and sleep blocks unless genuine emergency.
  - Keep text `≤130` characters and strictly sanitized (no medication names, financial amounts, or PII).
  - Always send a companion chat message alongside `?do=speak` as a persistent record.

### On-Demand Device Status (`?do=status`)
- Check the local status cache first (30-minute freshness window).
- Only poll when gating a concrete decision (e.g., pre-travel low-battery check or daytime sound-mode check)—never schedule curiosity polls or poll during quiet hours (10 PM–7 AM).
- Record `request_epoch` before firing `?do=status` and accept only a `phone-status` ingress record with `received_at > request_epoch` within 180 seconds. Timeout returns `"unknown"` (fail-open, never present stale data as current).

---

## 4. Building Macros from the Schema

Always generate `.macro` files programmatically with Python (`json.dump`) against the MacroDroid 6.0 serialization schema (see [`schema-essentials.md`](schema-essentials.md) for exact class and field definitions). Never hand-write raw JSON in chat or guess field names.

### Workflow & Output Contract
1. **Default to individual `.macro` files, never `.mdr` bundles:** Importing a `.mdr` file **replaces the user's entire macro set** on the phone. Always produce individual `.macro` files (which import alongside existing macros) unless a full bundle is explicitly requested.
2. **Wrapper structure:**
   ```json
   {
     "macroExportVersion": 1,
     "macro": {
       "m_name": "Universal Webhook",
       "m_enabled": true,
       "m_completed": true,
       "m_GUID": 0,
       "aiGenerated": 1,
       "m_description": "Universal webhook router for mode, speak, and status.",
       "localVariables": [],
       "m_triggerList": [],
       "m_actionList": [],
       "m_constraintList": []
     },
     "globalVariables": [],
     "userIcons": null,
     "aiFeedback": "After import, bind your routine steps inside the mode branches."
   }
   ```
3. **Critical Schema Rules:**
   - **Magic text dictionary syntax:** Put the dictionary key **inside** the curly braces: `{lv=qp[do]}` and `{lv=qp[m]}`. Never write `{lv=qp}[do]` (which expands to the entire dictionary string and silently fails comparisons).
   - **Flat `m_actionList`:** `IfConditionAction`, `ElseIfConditionAction`, `ElseAction`, and `EndIfAction` sit as siblings in the macro's flat `m_actionList` array. Never nest `m_actionList` inside an `IfConditionAction`. Every `If` chain must end with **exactly one** `EndIfAction`.
   - **Constraints vs. Actions:** Constraints (such as `MacroDroidVariableConstraint`) must **only** appear inside an `m_constraintList` (on the macro, trigger, action, or inside `IfConditionAction.m_constraintList`), never directly inside `m_actionList`.
   - **`localVariables` key name:** On the macro object, the local variables array is named `localVariables` (no `m_` prefix), whereas `m_triggerList`, `m_actionList`, and `m_constraintList` use the `m_` prefix.
   - **Secrets & UI Placeholders:** Inject webhook secrets from local gitignored config files at build time—never hardcode secrets in shared templates. For fields that lack MacroDroid magic text (such as ringer mode or screen-on state, or picker-captured Samsung Routine UUIDs), insert clearly labeled placeholders and note them in `m_description` and `aiFeedback`.
