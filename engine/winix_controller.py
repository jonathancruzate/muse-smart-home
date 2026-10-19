#!/usr/bin/env python3
"""winix_controller.py — semantic Winix purifier controller (standalone CLI).

Consumes home_context.py and applies standing purifier rules:
  - AUTO when sleeping, watching TV, or working (turbo too loud)
  - TURBO when away or idle (maximum allergy support)
  - PlasmaWave ALWAYS on, all purifiers, no exceptions

Only commands on change. Logs to the daily log on change.
State: hidden_files/winix-controller-state.json
"""
import json, os, subprocess, sys, time
from datetime import datetime

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
BIN = os.environ.get("SMART_HOME_BIN_DIR", SCRIPT_DIR if os.path.exists(os.path.join(SCRIPT_DIR, "home_context.py")) else os.path.join(GOAL, "bin"))
STATE_FILE = os.path.join(HIDDEN, "winix-controller-state.json")
DEVICES = {
    dev_id: name.capitalize()
    for dev_id, name in CFG.get("winix_purifiers", {
        "example-winix-device-001": "Kitchen",
        "example-winix-device-002": "Bedroom",
    }).items()
}
LIB = os.path.expanduser(os.environ.get("SMART_HOME_WINIX_LIB", PATHS.get("winix_lib", "~/workspace/skills/winix/lib")))
WINIX_CLI = os.path.expanduser(os.environ.get("SMART_HOME_WINIX_CLI", PATHS.get("winix_cli", "~/workspace/skills/winix/bin/winix")))

def get_context():
    p = subprocess.run([os.path.join(BIN, "home_context.py")],
                       capture_output=True, text=True, timeout=90)
    return json.loads(p.stdout)

def get_purifier_states():
    """Returns {device_id: {mode, name}} via winix watch."""
    p = subprocess.run([WINIX_CLI, "watch"],
                       capture_output=True, text=True, timeout=60)
    states = {}
    for line in p.stdout.strip().splitlines():
        # "Kitchen: power=on mode=auto fan=low aq=good filter=92.5% ..."
        parts = line.split(": ", 1)
        if len(parts) != 2:
            continue
        name, rest = parts
        kv = dict(kv.split("=", 1) for kv in rest.split(" ") if "=" in kv)
        for dev_id, dev_name in DEVICES.items():
            norm = lambda s: s.lower().replace(" ", "")
            if norm(dev_name) in norm(name):
                states[dev_id] = {"mode": kv.get("mode", "unknown"),
                                  "name": dev_name}
    return states

def set_purifier(dev_id, attr, value):
    sys.path.insert(0, LIB)
    from winix_client import control_saved
    try:
        r = control_saved(dev_id, attr, value)
        return bool(r.get("ok"))
    except Exception as e:
        print(f"control failed {dev_id} {attr}={value}: {e}", file=sys.stderr)
        return False

def get_plasma(dev_id):
    sys.path.insert(0, LIB)
    from winix_client import get_state
    try:
        s = get_state(dev_id)
        return s.get("plasmawave")
    except Exception:
        return None

def log_daily(msg):
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(HOME, f"memory/{today}.md")
        ts = datetime.now().strftime("%H:%M")
        with open(path, "a") as f:
            f.write(f"\n## {ts} — winix-semantic-controller\n{msg}\n")
    except Exception:
        pass

def main():
    dry = "--dry-run" in sys.argv
    ctx = get_context()
    reasons = []
    if ctx["sleep"]["asleep"]:
        desired, reasons = "auto", [f"sleeping ({ctx['sleep']['signal']})"]
    elif ctx["tv"]["on"]:
        desired, reasons = "auto", ["TV on"]
    elif ctx["work"]["working"]:
        desired, reasons = "auto", [f"working ({ctx['work']['signal']})"]
    elif ctx["location"]["zone"] != "home":
        desired, reasons = "turbo", ["away"]
    else:
        desired, reasons = "turbo", ["idle/default"]

    states = get_purifier_states()
    actions = []
    for dev_id, dev_name in DEVICES.items():
        cur = states.get(dev_id, {}).get("mode", "unknown")
        if cur == "unknown":
            actions.append(f"{dev_name}: state unreadable, skipped")
            continue
        if cur != desired:
            if dry:
                actions.append(f"{dev_name}: would set {cur} -> {desired}")
            else:
                target_mode = "manual" if desired == "turbo" else desired
                ok = set_purifier(dev_id, "mode", target_mode)
                if desired == "turbo" and ok:
                    ok = set_purifier(dev_id, "fan", "turbo") and ok
                actions.append(f"{dev_name}: {cur} -> {desired} ({'ok' if ok else 'FAILED'})")
        plasma = get_plasma(dev_id)
        if plasma == "off":
            if dry:
                actions.append(f"{dev_name}: plasma off, would turn on")
            else:
                ok = set_purifier(dev_id, "plasmawave", "on")
                actions.append(f"{dev_name}: plasma off -> on ({'ok' if ok else 'FAILED'})")

    changed = [a for a in actions if "->" in a and "would" not in a]
    summary = f"desired={desired} (reasons: {', '.join(reasons)}); " + ("; ".join(actions) if actions else "no changes")
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_run_utc": time.time(), "desired": desired,
                       "reasons": reasons, "actions": actions}, f)
    except Exception:
        pass
    if changed and not dry:
        log_daily(summary)
    print(summary)

if __name__ == "__main__":
    main()
