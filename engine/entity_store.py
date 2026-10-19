#!/usr/bin/env python3
"""entity_store.py — Home Assistant-style entity state machine.

The single source of truth for the automation layer. Every entity has one
canonical state; automations read this file, never raw APIs.

Usage: entity_store.py [--fresh]
Writes: hidden_files/entity-states.json
Schema: {"entity_id": {"state": str, "attributes": {...}, "last_updated": iso}}

Sources (cached where possible):
  home_context.py -> binary_sensor.alex_home/sleeping/working, media_player.smart_tv
  gh_devices.py   -> speaker.*, vacuum.robot_mop, humidifier.main, laundry.washer/dryer
  Winix direct    -> purifier.kitchen/bedroom (authoritative over GH link)
  SmartThings     -> sensor.* (multisensors), light.* (lamps), parallel fetch
"""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
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
BIN = os.environ.get("SMART_HOME_BIN_DIR", SCRIPT_DIR if os.path.exists(os.path.join(SCRIPT_DIR, "home_context.py")) else os.path.join(GOAL, "bin"))
HIDDEN = os.path.join(GOAL, "hidden_files")
STORE = os.path.join(HIDDEN, "entity-states.json")
LAST_CHANGED = os.path.join(HIDDEN, "entity-last-changed.json")
ST = os.path.expanduser(os.environ.get("SMART_HOME_ST_CLI", PATHS.get("st_cli", "~/workspace/skills/smartthings/bin/st")))
WINIX_LIB = os.path.expanduser(os.environ.get("SMART_HOME_WINIX_LIB", PATHS.get("winix_lib", "~/workspace/skills/winix/lib")))

OWNER_KEY = CFG.get("owner_key", "alex")
PET_KEY = CFG.get("pet_key", "pet")

SENSORS = CFG.get("sensors", {
    "example-st-sensor-001": "bedroom",
    "example-st-sensor-003": "living_room",
    "example-st-sensor-002": "front_door",
})
LAMPS = CFG.get("lamps", {
    "example-st-light-007": "night_table_lamp",
    "example-st-light-006": "living_room_lamp",
    "example-st-light-008": "hallway_light",
    "example-st-light-002": "dining_room_lamp",
    "example-st-light-001": "desk_lamp",
    "example-st-light-003": "kitchen_light_1",
})
WINIX_IDS = CFG.get("winix_purifiers", {
    "example-winix-device-001": "kitchen",
    "example-winix-device-002": "bedroom",
})

def run(cmd, timeout=25):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""

def st_status(dev_id):
    out = run([ST, "status", dev_id])
    try:
        return json.loads(out)["components"]["main"]
    except Exception:
        return {}

def log_daily(msg):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        ts = datetime.now().strftime("%H:%M")
        with open(os.path.join(HOME, f"memory/{today}.md"), "a") as f:
            f.write(f"\n## {ts} — couch-sleep-detector\n{msg}\n")
    except Exception:
        pass


def update_last_changed(entities, now_epoch):
    """Per-entity last-known-state tracking (hidden_files/entity-last-changed.json).

    Hardening rule (same as the engine): a failed/absent read must NEVER look
    like a state change. "unknown" states never move the stamp; only a known
    state differing from the last known state stamps a new change time.
    """
    try:
        lc = json.load(open(LAST_CHANGED))
    except Exception:
        lc = {}
    for eid, e in entities.items():
        state = e.get("state")
        prev = lc.get(eid, {})
        if state not in (None, "unknown") and prev.get("state") != state:
            lc[eid] = {"state": state, "changed_utc": now_epoch}
        elif eid not in lc:
            lc[eid] = {"state": state if state not in (None, "unknown") else "unknown",
                       "changed_utc": now_epoch}
    try:
        json.dump(lc, open(LAST_CHANGED, "w"))
    except Exception:
        pass
    return lc


def main():
    now = datetime.now(timezone.utc).isoformat()
    now_epoch = time.time()
    try:
        old_entities = json.load(open(STORE))["entities"]
    except Exception:
        old_entities = {}
    entities = {}
    def set_e(eid, state, attrs=None):
        entities[eid] = {"state": str(state), "attributes": attrs or {},
                         "last_updated": now}

    # --- context: presence / sleep / work / tv ---
    try:
        ctx = json.loads(run([os.path.join(BIN, "home_context.py")], timeout=90))
        oh = ctx["whos_home"].get(OWNER_KEY)
        set_e(f"binary_sensor.{OWNER_KEY}_home", "on" if oh else "off",
              {"zone": ctx["location"]["zone"], "age_min": ctx["location"]["age_min"]})
        set_e("binary_sensor.sleeping", "on" if ctx["sleep"]["asleep"] else "off",
              {"signal": ctx["sleep"]["signal"]})
        set_e("binary_sensor.working", "on" if ctx["work"]["working"] else "off",
              {"signal": ctx["work"]["signal"]})
        set_e("media_player.smart_tv", "on" if ctx["tv"]["on"] else "off",
              {"source": ctx["tv"]["source"]})
        ph = ctx["whos_home"].get(PET_KEY)
        set_e(f"binary_sensor.{PET_KEY}_home",
              "on" if ph is True else "off" if ph is False else "unknown",
              {"signal": ctx.get("pet_signal", "unknown")})
        set_e("sensor.time_bucket", ctx["time_bucket"], {})
    except Exception as e:
        set_e("binary_sensor.context_error", "on", {"error": str(e)[:100]})

    # --- GH-exclusive devices ---
    try:
        gh = json.loads(run([os.path.join(BIN, "gh_devices.py")], timeout=90))
        for key, d in gh.items():
            online = d.get("online", False)
            if key.startswith("speaker_"):
                room = key.split("_", 1)[1]
                set_e(f"speaker.{room}", "on" if online else "off",
                      {"display_name": d["display_name"]})
            elif key == "robot_mop":
                set_e("vacuum.robot_mop", "on" if online else "unknown",
                      {"display_name": d["display_name"], **d.get("state", {})})
            elif key == "humidifier":
                st8 = d.get("state", {})
                set_e("humidifier.main",
                      "on" if st8.get("SimplifiedOnOff.onOff") == "ON_OFF_ENUM_ON" else "unknown" if not online else "off",
                      {"online": online, **st8})
            elif key in ("washer", "dryer"):
                st8 = d.get("state", {})
                running = st8.get("SimplifiedOnOff.onOff") == "ON_OFF_ENUM_ON"
                set_e(f"laundry.{key}", "on" if running else "unknown" if not online else "off",
                      {"online": online, **st8})
            elif key == "streaming_tv":
                st8 = d.get("state", {})
                def _on(v): return v is True or v == "ON_OFF_ENUM_ON"
                on = any(_on(st8.get(k)) for k in (
                    "GoogleTvDevice/OnOff.onOff",
                    "SimplifiedOnOff.onOff",
                    "SpeakerDevice/OnOff.onOff"))
                set_e("media_player.streaming_tv",
                      "unknown" if not online else "on" if on else "off",
                      {"display_name": d["display_name"], "online": online, **st8})
    except Exception as e:
        set_e("binary_sensor.gh_error", "on", {"error": str(e)[:100]})

    # --- Winix purifiers (direct API, authoritative) ---
    sys.path.insert(0, WINIX_LIB)
    try:
        from winix_client import get_state, interpret
        for dev_id, name in WINIX_IDS.items():
            try:
                raw = get_state(dev_id)
                s = interpret(raw.get("attributes", {}))
                mode = s.get("mode", "unknown")
                set_e(f"purifier.{name}", mode,
                      {"fan": s.get("fan"), "plasmawave": s.get("plasmawave"),
                       "air_quality": s.get("air_quality"),
                       "filter_remaining_pct": s.get("filter_remaining_pct")})
            except Exception:
                set_e(f"purifier.{name}", "unknown", {})
    except Exception as e:
        set_e("binary_sensor.winix_error", "on", {"error": str(e)[:100]})

    # --- SmartThings sensors + lamps (parallel) ---
    def fetch_sensor(item):
        dev_id, name = item
        c = st_status(dev_id)
        out = {}
        try:
            out["humidity"] = c["relativeHumidityMeasurement"]["humidity"]["value"]
        except Exception:
            pass
        try:
            out["illuminance"] = c["illuminanceMeasurement"]["illuminance"]["value"]
        except Exception:
            pass
        try:
            out["temperature"] = c["temperatureMeasurement"]["temperature"]["value"]
        except Exception:
            pass
        try:
            out["motion"] = c["motionSensor"]["motion"]["value"]
        except Exception:
            pass
        return name, out

    def fetch_lamp(item):
        dev_id, name = item
        c = st_status(dev_id)
        try:
            return name, c["switch"]["switch"]["value"]
        except Exception:
            return name, "unknown"

    with ThreadPoolExecutor(max_workers=10) as ex:
        for name, vals in ex.map(fetch_sensor, SENSORS.items()):
            if "humidity" in vals:
                set_e(f"sensor.{name}_humidity", vals["humidity"], {"unit": "%"})
            if "illuminance" in vals:
                set_e(f"sensor.{name}_illuminance", vals["illuminance"], {"unit": "lux"})
            if "temperature" in vals:
                set_e(f"sensor.{name}_temperature", vals["temperature"], {"unit": "C"})
            if "motion" in vals:
                set_e(f"binary_sensor.{name}_motion", "on" if vals["motion"] == "active" else "off", {})
    with ThreadPoolExecutor(max_workers=8) as ex:
        for name, val in ex.map(fetch_lamp, LAMPS.items()):
            set_e(f"light.{name}", val, {})

    # --- couch-sleep detector + phone-activity signal ---
    # Runs on the just-built entities; writes binary_sensor.couch_sleep and
    # sensor.phone_quiet. Failures leave the entity "unknown", never silently off.
    try:
        det = json.loads(run([os.path.join(BIN, "couch_sleep.py")], timeout=90))
        cs_state = det.get("state", "unknown")
        cs_attrs = det.get("attributes", {})
    except Exception as e:
        cs_state, cs_attrs = "unknown", {"error": str(e)[:100]}
    set_e("binary_sensor.couch_sleep", cs_state, cs_attrs)
    try:
        pq = json.load(open(os.path.join(HIDDEN, "phone-activity-state.json")))
        pqm = pq.get("phone_quiet_min")
        set_e("sensor.phone_quiet",
              str(round(pqm)) if pqm is not None else "unknown",
              {"unit": "min", "signal": pq.get("phone_signal"),
               "notif_arrivals_60min": pq.get("notif_arrivals_60min")})
    except Exception:
        set_e("sensor.phone_quiet", "unknown", {})

    # last-changed sidecar (after all entities incl. couch_sleep are set, so
    # on_for_min below reads this poll's stamp)
    lc = update_last_changed(entities, now_epoch)
    try:
        changed_utc = lc.get("binary_sensor.couch_sleep", {}).get("changed_utc", now_epoch)
        entities["binary_sensor.couch_sleep"]["attributes"]["on_for_min"] = \
            round((now_epoch - changed_utc) / 60, 1)
    except Exception:
        pass

    # log couch_sleep transitions (the engine only logs automation actions)
    old_cs = old_entities.get("binary_sensor.couch_sleep", {}).get("state")
    if old_cs != cs_state and old_cs is not None:
        sig = (cs_attrs.get("signals") or {})
        log_daily(f"couch_sleep {old_cs} -> {cs_state} "
                  f"(confidence {cs_attrs.get('confidence')}, "
                  f"motion_quiet {(sig.get('motion') or {}).get('quiet_min')}m, "
                  f"phone_quiet {(sig.get('phone') or {}).get('quiet_min')}m, "
                  f"tv_on {(sig.get('tv') or {}).get('on_min')}m)")

    try:
        with open(STORE, "w") as f:
            json.dump({"updated_utc": now, "entities": entities}, f)
    except Exception:
        pass
    print(json.dumps({"updated_utc": now, "entity_count": len(entities)}))

if __name__ == "__main__":
    main()
