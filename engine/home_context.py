#!/usr/bin/env python3
"""home_context.py — shared context engine for the smart-home automation layer.

Gathers the current home situation once, caches it briefly, and prints JSON.
Every controller (air purifiers, humidity, lights, robot mop, ...) consumes this
instead of re-reading sensors itself.

Usage: home_context.py [--fresh]   # --fresh bypasses the cache
Cache: hidden_files/home-context-cache.json (TTL 5 min)

Context schema:
{
  "ts_utc": ..., "ts_local": ...,
  "location": {"zone": "home|away|unknown", "age_min": N},
  "st_mode": "Home|Night|Away|...",
  "sleep": {"asleep": bool, "signal": "night_mode|sleep_block|none"},
  "tv": {"on": bool, "source": "smartthings"},
  "work": {"working": bool, "signal": "calendar_block|none"},
  "whos_home": {"alex": bool|null, "partner": bool|null, "pet": bool|null},
  "time_bucket": "night|morning|day|evening|wind_down"
}
Unknowns are null, never guessed. Callers fail toward safe defaults.
"""
import json, os, subprocess, sys, time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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
CACHE = os.path.join(HIDDEN, "home-context-cache.json")
CACHE_TTL = 300

# Explicit household timezone. Background hooks often run with the VM default
# (UTC), so datetime.now().astimezone() would compute time-based signals in the
# wrong timezone unless pinned explicitly.
LOCAL_TZ = ZoneInfo(os.environ.get("SMART_HOME_TIMEZONE", CFG.get("timezone", "America/New_York")))
ST = os.path.expanduser(os.environ.get("SMART_HOME_ST_CLI", PATHS.get("st_cli", "~/workspace/skills/smartthings/bin/st")))
LOCATION_STATE_FILE = os.path.expanduser(os.environ.get("SMART_HOME_LOCATION_STATE", PATHS.get("location_state", "~/workspace/location/hidden_files/location-state.json")))

OWNER_KEY = CFG.get("owner_key", "alex")
PET_KEY = CFG.get("pet_key", "pet")
HOME_LOCATION_ID = os.environ.get("SMART_HOME_LOCATION_ID", CFG.get("home_location_id", "example-st-location-001"))
PRIMARY_TV_ID = os.environ.get("SMART_HOME_TV_ST_ID", CFG.get("primary_tv_st_id", "example-st-tv-001"))

# Home anchor for the pet collar SmartTag geofence (replace 0.0, 0.0 with your home coordinates).
PET_HOME_LAT = float(os.environ.get("SMART_HOME_PET_LAT", CFG.get("pet_home_lat", 0.0)))
PET_HOME_LNG = float(os.environ.get("SMART_HOME_PET_LNG", CFG.get("pet_home_lng", 0.0)))
PET_GEOFENCE_M = float(os.environ.get("SMART_HOME_PET_GEOFENCE_M", CFG.get("pet_geofence_radius_m", 250)))
PET_COLLAR_ID = os.environ.get("SMART_HOME_PET_COLLAR_ID", CFG.get("pet_collar_st_id", "example-st-collar-001"))

def _haversine_m(lat1, lon1, lat2, lon2):
    from math import radians, sin, cos, asin, sqrt
    R = 6371000.0
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))

def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout.strip()
    except Exception:
        return ""

def read_location():
    try:
        with open(LOCATION_STATE_FILE) as f:
            s = json.load(f)
        zone = s.get("zone", "unknown")
        ts = s.get("timestamp_utc", 0)
        try:
            ts_epoch = float(ts)
        except (TypeError, ValueError):
            try:
                ts_epoch = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
            except Exception:
                ts_epoch = 0
        age_min = (time.time() - ts_epoch) / 60 if ts_epoch else 9999
        if age_min > 240:  # expired
            zone = "unknown"
        return {"zone": zone, "age_min": round(age_min, 1)}
    except Exception:
        return {"zone": "unknown", "age_min": 9999}

def read_st_mode():
    out = run([ST, "mode-current", HOME_LOCATION_ID])
    try:
        d = json.loads(out)
        return d.get("label") or d.get("name") or "unknown"
    except Exception:
        return "unknown"

def read_tv():
    out = run([ST, "status", PRIMARY_TV_ID])
    try:
        d = json.loads(out)
        sw = d["components"]["main"]["switch"]["switch"]["value"]
        return {"on": sw == "on", "source": "smartthings"}
    except Exception:
        return {"on": False, "source": "unknown"}

def calendar_now():
    """Best-effort: events active right now. Returns list of summaries (lowercased)."""
    try:
        now = datetime.now(timezone.utc)
        tmin = now.isoformat().replace("+00:00", "Z")
        tmax = datetime.fromtimestamp(now.timestamp() + 60, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        out = run(["hatch_gws_cli", "calendar", "events", "list", "--params",
                   json.dumps({"calendarId": "primary", "timeMin": tmin, "timeMax": tmax,
                               "singleEvents": True, "maxResults": 10})], timeout=25)
        d = json.loads(out)
        items = d.get("items", d.get("result", {}).get("items", []))
        return [(i.get("summary", "") or "").lower() for i in items]
    except Exception:
        return []

def main():
    if "--fresh" not in sys.argv and os.path.exists(CACHE):
        try:
            with open(CACHE) as f:
                c = json.load(f)
            if time.time() - c.get("cache_ts", 0) < CACHE_TTL:
                print(json.dumps(c["context"]))
                return
        except Exception:
            pass

    loc = read_location()
    st_mode = read_st_mode()
    tv = read_tv()

    # Sleep: ST Night mode is the fast reliable signal; calendar Sleep block as backup.
    asleep, sleep_signal = False, "none"
    if st_mode == "Night":
        asleep, sleep_signal = True, "night_mode"
    else:
        summaries = calendar_now()
        if any("sleep" in s for s in summaries):
            asleep, sleep_signal = True, "sleep_block"

    # Work: only evaluated when it could matter (home, daytime weekday, not asleep, tv off).
    working, work_signal = False, "none"
    now_local = datetime.now(LOCAL_TZ)
    wd, hr = now_local.weekday(), now_local.hour
    if (loc["zone"] == "home" and not asleep and not tv["on"]
            and wd < 5 and 9 <= hr < 18):
        summaries = summaries if 'summaries' in dir() else calendar_now()
        work_kw = ("work", "focus", "deep work", "office", "meeting", "standup", "1:1", "sync")
        if any(any(k in s for k in work_kw) for s in summaries):
            working, work_signal = True, "calendar_block"

    # Who's home.
    # Pet presence is SmartTag collar-backed when fresh (<120 min) via a home
    # geofence. If the collar read is stale or missing (e.g. dead battery or
    # missed poll), default to assuming the pet is home rather than unknown so
    # comfort automations fail safe.
    owner_home = loc["zone"] == "home" if loc["zone"] != "unknown" else None
    pet_home, pet_signal = True, "assumed-home"
    try:
        with open(os.path.join(HIDDEN, "pet-collar.json")) as f:
            pc = json.load(f)
        age_min = (time.time() - float(pc.get("checked_utc", 0))) / 60
        fresh = age_min < 120 and pc.get("lat") is not None and pc.get("lng") is not None and not pc.get("stale")
        if fresh and (PET_HOME_LAT != 0.0 or PET_HOME_LNG != 0.0):
            dist_m = _haversine_m(float(pc["lat"]), float(pc["lng"]),
                                  PET_HOME_LAT, PET_HOME_LNG)
            pet_home = dist_m < PET_GEOFENCE_M
            pet_signal = f"collar:{int(dist_m)}m:{int(age_min)}min"
        else:
            pet_signal = f"assumed-home:collar-stale:{int(age_min)}min"
    except Exception:
        pass

    if hr < 5: bucket = "night"
    elif hr < 12: bucket = "morning"
    elif hr < 17: bucket = "day"
    elif hr < 22: bucket = "evening"
    else: bucket = "wind_down"

    ctx = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "ts_local": now_local.isoformat(),
        "location": loc,
        "st_mode": st_mode,
        "sleep": {"asleep": asleep, "signal": sleep_signal},
        "tv": tv,
        "work": {"working": working, "signal": work_signal},
        "whos_home": {OWNER_KEY: owner_home, "partner": None, PET_KEY: pet_home},
        "pet_signal": pet_signal,
        "time_bucket": bucket,
    }
    try:
        with open(CACHE, "w") as f:
            json.dump({"cache_ts": time.time(), "context": ctx}, f)
    except Exception:
        pass
    print(json.dumps(ctx))

if __name__ == "__main__":
    main()
