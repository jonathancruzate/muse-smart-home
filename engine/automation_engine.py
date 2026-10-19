#!/usr/bin/env python3
"""automation_engine.py — Home Assistant-style declarative automation engine.

Automations live as YAML in hidden_files/automations/*.yaml:
  - alias: unique name
    trigger:
      - platform: interval      # every N minutes
        minutes: 10
      - platform: state         # entity changed (optionally to a value)
        entity_id: laundry.washer
        to: 'off'
    condition:                   # all must hold (optional)
      - condition: state
        entity_id: binary_sensor.alex_home
        state: 'on'
    action:
      - service: winix.set_mode
        target: {entity_id: [purifier.kitchen]}
        data: {mode: "{{ 'auto' if is_state('binary_sensor.sleeping','on') else 'turbo' }}"}

Templates: {{ python expression }} with states{}, is_state(), state_attr().
Services: winix.set_mode, notify.broadcast, light.turn_on/off, humidifier.turn_on/off,
  media_player.turn_off, media_player.play_pet_music.

Usage: automation_engine.py [--dry-run] [--hook]
State: hidden_files/automation-engine-state.json (last triggers, last states).
"""
import json, os, re, subprocess, sys, time
from datetime import datetime, timezone

HOME = os.path.expanduser("~")
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_config():
    cfg_path = os.environ.get("SMART_HOME_CONFIG")
    if not cfg_path:
        cand = os.path.join(SCRIPT_DIR, "config.json")
        cfg_path = cand if os.path.exists(cand) else os.path.join(SCRIPT_DIR, "config.example.json")
    try:
        with open(os.path.expanduser(cfg_path)) as f:
            return json.load(f)
    except Exception:
        return {}

CFG = load_config()
PATHS = CFG.get("paths", {})
GOAL = os.path.expanduser(os.environ.get("SMART_HOME_GOAL_DIR", PATHS.get("goal_dir", "~/workspace/goals/manage-my-smart-home")))
BIN = os.environ.get("SMART_HOME_BIN_DIR", SCRIPT_DIR if os.path.exists(os.path.join(SCRIPT_DIR, "entity_store.py")) else os.path.join(GOAL, "bin"))
HIDDEN = os.path.join(GOAL, "hidden_files")
AUTO_DIR = os.environ.get("SMART_HOME_AUTOMATIONS_DIR", os.path.join(HIDDEN, "automations"))
ENGINE_STATE = os.path.join(HIDDEN, "automation-engine-state.json")
ENTITY_STORE = os.path.join(HIDDEN, "entity-states.json")
ST = os.path.expanduser(os.environ.get("SMART_HOME_ST_CLI", PATHS.get("st_cli", "~/workspace/skills/smartthings/bin/st")))
GH = os.path.expanduser(os.environ.get("SMART_HOME_GH_CLI", PATHS.get("gh_cli", "~/workspace/skills/googlehome/bin/gh")))
WINIX_LIB = os.path.expanduser(os.environ.get("SMART_HOME_WINIX_LIB", PATHS.get("winix_lib", "~/workspace/skills/winix/lib")))

try:
    import yaml
except ImportError:
    yaml = None

# entity -> control identifiers (loaded from config / example defaults)
WINIX_IDS = {
    f"purifier.{name}": dev_id
    for dev_id, name in CFG.get("winix_purifiers", {
        "example-winix-device-001": "kitchen",
        "example-winix-device-002": "bedroom",
    }).items()
}
LAMP_ST_IDS = {
    f"light.{name}": dev_id
    for dev_id, name in CFG.get("lamps", {
        "example-st-light-007": "night_table_lamp",
        "example-st-light-006": "living_room_lamp",
        "example-st-light-008": "hallway_light",
        "example-st-light-002": "dining_room_lamp",
        "example-st-light-001": "desk_lamp",
        "example-st-light-003": "kitchen_light_1",
    }).items()
}
SPEAKER_NAMES = CFG.get("speakers", {
    "speaker.bathroom": "Bathroom Speaker",
    "speaker.bedroom": "Bedroom Display",
    "speaker.dining": "Dining Room Speaker",
    "speaker.kitchen": "Kitchen Speaker",
    "speaker.living": "Living Room Speaker",
})
TV_IDS = CFG.get("tv_st_ids", {
    "media_player.smart_tv": "example-st-tv-001",
})
# Google TV / streaming box (GH-only, not in SmartThings): entity -> GH display name
GH_TV_NAMES = CFG.get("gh_tv_names", {
    "media_player.streaming_tv": "Living Room Streaming TV",
})
PET_MUSIC_QUERY = CFG.get("pet_music_query", "play gentle pet comfort music on YouTube")

def log_daily(msg):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%H:%M")
        with open(os.path.join(HOME, f"memory/{today}.md"), "a") as f:
            f.write(f"\n## {ts} — automation-engine\n{msg}\n")
    except Exception:
        pass

def run(cmd, timeout=30):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.stdout.strip()

# ---------- templates ----------
def render_template(tmpl, states, attrs):
    def repl(m):
        expr = m.group(1).strip()
        ns = {"states": states, "attrs": attrs,
              "is_state": lambda e, s: states.get(e) == s,
              "state_attr": lambda e, a: (attrs.get(e) or {}).get(a)}
        try:
            return str(eval(expr, {"__builtins__": {}}, ns))
        except Exception as e:
            return f"TEMPLATE_ERROR:{e}"
    return re.sub(r"\{\{\s*(.*?)\s*\}\}", repl, tmpl)

def render_data(data, states, attrs):
    if isinstance(data, dict):
        return {k: render_data(v, states, attrs) for k, v in data.items()}
    if isinstance(data, list):
        return [render_data(v, states, attrs) for v in data]
    if isinstance(data, str) and "{{" in data:
        return render_template(data, states, attrs)
    return data

# ---------- services ----------
def svc_winix_set_mode(entity_ids, data, states, attrs, dry):
    sys.path.insert(0, WINIX_LIB)
    from winix_client import control_saved
    mode = data.get("mode", "auto")
    out = []
    for eid in entity_ids:
        dev = WINIX_IDS.get(eid)
        if not dev:
            out.append(f"{eid}: no winix mapping"); continue
        cur_mode = states.get(eid)
        cur_fan = (attrs or {}).get(eid, {}).get("fan")
        try:
            if mode == "turbo":
                # "turbo" is a fan-speed value (A04), not a Winix mode (A03).
                # Semantic turbo = manual mode + fan speed turbo.
                if cur_mode == "manual" and cur_fan == "turbo":
                    out.append(f"{eid}: already turbo"); continue
                if dry:
                    out.append(f"{eid}: would set {cur_mode}/{cur_fan} -> turbo"); continue
                ok = control_saved(dev, "mode", "manual").get("ok", False)
                ok = control_saved(dev, "fan", "turbo").get("ok", False) and ok
                control_saved(dev, "plasmawave", "on")
                out.append(f"{eid}: {cur_mode}/{cur_fan} -> turbo ({'ok' if ok else 'FAILED'})")
            elif mode in ("auto", "manual"):
                if cur_mode == mode:
                    out.append(f"{eid}: already {mode}"); continue
                if dry:
                    out.append(f"{eid}: would set {cur_mode} -> {mode}"); continue
                ok = control_saved(dev, "mode", mode).get("ok", False)
                control_saved(dev, "plasmawave", "on")
                out.append(f"{eid}: {cur_mode} -> {mode} ({'ok' if ok else 'FAILED'})")
            else:
                out.append(f"{eid}: unsupported mode {mode!r}")
        except Exception as e:
            out.append(f"{eid}: FAILED ({e})")
    return out

def svc_notify_broadcast(entity_ids, data, states, attrs, dry):
    msg = data.get("message", "")
    out = []
    for eid in entity_ids:
        name = SPEAKER_NAMES.get(eid)
        if not name:
            out.append(f"{eid}: no speaker mapping"); continue
        if states.get(eid) != "on":
            out.append(f"{eid}: offline, skipped"); continue
        if dry:
            out.append(f"{eid}: would broadcast: {msg[:60]}"); continue
        r = run([GH, "action", name, "AssistantBroadcast.Broadcast",
                 json.dumps({"msg": msg})], timeout=60)
        out.append(f"{eid}: broadcast sent ({'ok' if r else 'no output'})")
    return out

def svc_light(entity_ids, turn_on, states, dry):
    out = []
    for eid in entity_ids:
        dev = LAMP_ST_IDS.get(eid)
        if not dev:
            out.append(f"{eid}: no ST mapping"); continue
        want = "on" if turn_on else "off"
        if states.get(eid) == want:
            out.append(f"{eid}: already {want}"); continue
        if dry:
            out.append(f"{eid}: would turn {want}"); continue
        r = run([ST, "command", dev, "switch", want], timeout=30)
        out.append(f"{eid}: -> {want} ({'ok' if r else 'no output'})")
    return out

def svc_humidifier(entity_ids, turn_on, dry):
    out = []
    want = "On" if turn_on else "Off"
    for eid in entity_ids:
        if dry:
            out.append(f"{eid}: would turn {want}"); continue
        r = run([GH, "action", "Humidifier", f"OnOff.{want}"], timeout=60)
        out.append(f"{eid}: -> {want} ({'ok' if r else 'no output'})")
    return out

def svc_media_player_turn_off(entity_ids, data, states, attrs, dry):
    out = []
    for eid in entity_ids:
        if eid in GH_TV_NAMES:
            # GH-only TV: turn off via Google Home API
            if states.get(eid) == "off":
                out.append(f"{eid}: already off"); continue
            if dry:
                out.append(f"{eid}: would turn off via GH"); continue
            r = run([GH, "action", GH_TV_NAMES[eid], "GoogleTvDevice/OnOff.Off"], timeout=30)
            out.append(f"{eid}: -> off via GH ({'ok' if r else 'no output'})")
            continue
        dev = TV_IDS.get(eid)
        if not dev:
            out.append(f"{eid}: no TV mapping"); continue
        if states.get(eid) == "off":
            out.append(f"{eid}: already off"); continue
        if dry:
            out.append(f"{eid}: would turn off"); continue
        r = run([ST, "command", dev, "switch", "off"], timeout=30)
        out.append(f"{eid}: on -> off ({'ok' if r else 'no output'})")
    return out

def svc_media_player_play_pet_music(entity_ids, data, states, attrs, dry):
    # Pet comfort: gentle comfort music on YouTube via Google TV device.
    # Uses the AssistantFulfillment.OkGoogle free-text trait.
    # Sequence: power on first (query won't launch YouTube on a sleeping TV),
    # then issue the playback query.
    out = []
    query = (data or {}).get("query", PET_MUSIC_QUERY)
    for eid in entity_ids:
        name = GH_TV_NAMES.get(eid)
        if not name:
            out.append(f"{eid}: no GH TV mapping"); continue
        if dry:
            out.append(f"{eid}: would power on + play '{query}'"); continue
        r1 = run([GH, "action", name, "GoogleTvDevice/OnOff.On"], timeout=30)
        time.sleep(8)  # let the TV wake before the query
        r2 = run([GH, "action", name, "AssistantFulfillment.OkGoogle",
                  json.dumps({"query": query})], timeout=45)
        ok = bool(r2) and "homeActionResponses" in r2
        out.append(f"{eid}: power {'ok' if r1 else 'no output'}; "
                   f"play '{query}' ({'ok' if ok else 'no confirmation'})")
    return out

SERVICES = {
    "winix.set_mode": svc_winix_set_mode,
    "notify.broadcast": svc_notify_broadcast,
    "light.turn_on": lambda e, d, s, a, dry: svc_light(e, True, s, dry),
    "light.turn_off": lambda e, d, s, a, dry: svc_light(e, False, s, dry),
    "humidifier.turn_on": lambda e, d, s, a, dry: svc_humidifier(e, True, dry),
    "humidifier.turn_off": lambda e, d, s, a, dry: svc_humidifier(e, False, dry),
    "media_player.turn_off": svc_media_player_turn_off,
    "media_player.play_pet_music": svc_media_player_play_pet_music,
}

# ---------- engine ----------
def main():
    if yaml is None:
        print(json.dumps({"fired": [], "errors": ["pyyaml not installed"]})); return
    dry = "--dry-run" in sys.argv
    hook = "--hook" in sys.argv
    run([os.path.join(BIN, "entity_store.py")], timeout=120)
    try:
        store = json.load(open(ENTITY_STORE))
    except Exception as e:
        print(f"entity store unreadable: {e}"); return
    entities = store["entities"]
    states = {eid: e["state"] for eid, e in entities.items()}
    attrs = {eid: e["attributes"] for eid, e in entities.items()}

    try:
        estate = json.load(open(ENGINE_STATE))
    except Exception:
        estate = {"last_trigger": {}, "last_states": {}}
    now = time.time()
    fired = []

    for fname in sorted(os.listdir(AUTO_DIR)):
        if not fname.endswith((".yaml", ".yml")):
            continue
        try:
            automations = yaml.safe_load(open(os.path.join(AUTO_DIR, fname))) or []
        except Exception as e:
            fired.append(f"{fname}: YAML error {e}"); continue
        for auto in automations:
            alias = auto.get("alias", fname)
            # --- triggers ---
            triggered, why = False, ""
            for t in auto.get("trigger", []):
                p = t.get("platform")
                if p == "interval":
                    mins = t.get("minutes", 10)
                    last = estate["last_trigger"].get(alias, 0)
                    if now - last >= mins * 60:
                        triggered, why = True, f"interval {mins}m"; break
                elif p == "state":
                    eid = t.get("entity_id")
                    cur = states.get(eid)
                    prev = estate["last_states"].get(eid)
                    want = t.get("to")
                    # Hardened rule: a failed/absent read must NEVER look like a
                    # state change. Unknown/None on either side holds last state.
                    if prev in (None, "unknown") or cur in (None, "unknown"):
                        continue
                    if cur != prev and (want is None or cur == want):
                        triggered, why = True, f"{eid} {prev}->{cur}"; break
            if not triggered:
                continue
            # --- conditions ---
            ok = True
            for c in auto.get("condition", []):
                if c.get("condition") == "state":
                    if states.get(c["entity_id"]) != c["state"]:
                        ok = False; break
                elif c.get("condition") == "template":
                    val = render_template(c.get("value_template", ""), states, attrs)
                    if val not in ("True", "true", "1"):
                        ok = False; break
            if not ok:
                continue
            # --- actions ---
            results = []
            for a in auto.get("action", []):
                svc = SERVICES.get(a.get("service"))
                if not svc:
                    results.append(f"unknown service {a.get('service')}"); continue
                tgt = a.get("target", {}).get("entity_id", [])
                if isinstance(tgt, str):
                    tgt = [tgt]
                data = render_data(a.get("data", {}), states, attrs)
                try:
                    results.extend(svc(tgt, data, states, attrs, dry))
                except Exception as e:
                    results.append(f"{a.get('service')} error: {e}")
            estate["last_trigger"][alias] = now
            changed = [r for r in results if "->" in r and "already" not in r and "would" not in r]
            errors = [r for r in results if "FAILED" in r or "error" in r.lower() or "TEMPLATE_ERROR" in r]
            broadcasts = sum(1 for r in results if "broadcast sent" in r)
            fired.append({"alias": alias, "why": why, "actions": results,
                          "changed": changed, "errors": errors, "broadcasts": broadcasts})
            if changed and not dry:
                log_daily(f"{alias} [{why}]\n" + "\n".join(f"- {r}" for r in changed))

    estate["last_states"] = states
    if not dry:
        try:
            json.dump(estate, open(ENGINE_STATE, "w"))
        except Exception:
            pass
    if hook:
        print(json.dumps({"fired": fired}))
    else:
        lines = []
        for f in fired:
            lines.append(f"{f['alias']} [{f['why']}]: " + ("; ".join(f["actions"]) if f["actions"] else "no-op"))
        print("\n".join(lines) if lines else "no automations triggered")

if __name__ == "__main__":
    main()
