#!/usr/bin/env python3
"""gh_devices.py — Google Home-exclusive device state reader.

Covers devices that exist ONLY in Google Home (not in SmartThings), so
cross-platform automations can see them:
  speakers (5): Bathroom, Bedroom Display, Dining Room, Kitchen, Living Room
  robot mop, Humidifier (cloud link), Washer, Dryer
  Streaming TV (GoogleTvDevice)

Usage: gh_devices.py [--fresh]   # --fresh bypasses the 5-min cache
Prints JSON: {device_key: {display_name, gh_id, online, state_digest}}
Missing from the snapshot = offline/unreachable (GH omits dead devices).
Cache: hidden_files/gh-devices-cache.json
"""
import json, os, subprocess, sys, time

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
HIDDEN = os.path.join(GOAL, "hidden_files")
CACHE = os.path.join(HIDDEN, "gh-devices-cache.json")
CACHE_TTL = 300
GH = os.path.expanduser(os.environ.get("SMART_HOME_GH_CLI", PATHS.get("gh_cli", "~/workspace/skills/googlehome/bin/gh")))

# GH-exclusive devices: key -> (display name, GH device id)
DEFAULT_EXCLUSIVE = {
    "speaker_bathroom": {"display_name": "Bathroom Speaker", "gh_id": "device@example-gh-speaker-001"},
    "speaker_bedroom": {"display_name": "Bedroom Display", "gh_id": "device@example-gh-speaker-002"},
    "speaker_dining": {"display_name": "Dining Room Speaker", "gh_id": "device@example-gh-speaker-003"},
    "speaker_kitchen": {"display_name": "Kitchen Speaker", "gh_id": "device@example-gh-speaker-004"},
    "speaker_living": {"display_name": "Living Room Speaker", "gh_id": "device@example-gh-speaker-005"},
    "robot_mop": {"display_name": "Robot Mop", "gh_id": "device@example-gh-mop-001"},
    "humidifier": {"display_name": "Humidifier", "gh_id": "device@example-gh-humidifier-001"},
    "washer": {"display_name": "Washer", "gh_id": "device@example-gh-washer-001"},
    "dryer": {"display_name": "Dryer", "gh_id": "device@example-gh-dryer-001"},
    "streaming_tv": {"display_name": "Living Room Streaming TV", "gh_id": "device@example-gh-streamtv-001"},
}
EXCLUSIVE = {
    k: (v["display_name"], v["gh_id"])
    for k, v in CFG.get("gh_exclusive", DEFAULT_EXCLUSIVE).items()
}

INTERESTING = ("onOff", "measuredValue", "currentModeSettings", "currentToggleSettings",
               "relativeHumiditySetpoint", "currentFanMode", "customFanMode")

def snapshot():
    p = subprocess.run([GH, "state"], capture_output=True, text=True, timeout=90)
    d = json.loads(p.stdout)
    found = {}
    def walk(o):
        if isinstance(o, dict):
            if "displayName" in o and "traits" in o:
                found[o["displayName"]] = o
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(d)
    return found

def digest(dev):
    out = {}
    for trait, tv in (dev.get("traits") or {}).items():
        st = tv.get("state") or {}
        for k in INTERESTING:
            if k in st and st[k] not in (None, "", {}, []):
                out[f"{trait}.{k}"] = st[k]
    return out

def main():
    if "--fresh" not in sys.argv and os.path.exists(CACHE):
        try:
            with open(CACHE) as f:
                c = json.load(f)
            if time.time() - c.get("cache_ts", 0) < CACHE_TTL:
                print(json.dumps(c["devices"]))
                return
        except Exception:
            pass
    try:
        snap = snapshot()
    except Exception as e:
        print(json.dumps({"error": f"gh state failed: {e}"}))
        return
    result = {}
    for key, (name, gid) in EXCLUSIVE.items():
        dev = snap.get(name)
        if dev is None:
            result[key] = {"display_name": name, "gh_id": gid,
                           "online": False, "note": "absent from GH snapshot (offline/unreachable)"}
        else:
            conn = dev.get("resourceConnectivityState", "UNKNOWN")
            result[key] = {"display_name": name, "gh_id": gid,
                           "online": conn == "ONLINE",
                           "connectivity": conn, "state": digest(dev)}
    try:
        with open(CACHE, "w") as f:
            json.dump({"cache_ts": time.time(), "devices": result}, f)
    except Exception:
        pass
    print(json.dumps(result))

if __name__ == "__main__":
    main()
