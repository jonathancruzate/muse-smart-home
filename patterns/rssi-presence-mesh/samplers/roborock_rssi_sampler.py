#!/usr/bin/env python3
"""Robot-vacuum RSSI sampler — the mobile point for the RSSI series
(pattern leg 3 of 4).

The trick: a Roborock robot vacuum answers the RPC
`GET_NETWORK_INFO` on its LOCAL protocol channel with its own AP
link details — SSID, BSSID, frequency, and RSSI. Most integrations
never ask. Two products from one read:

  1. Series point `roborock-ap-main` in rssi-series.jsonl — while
     docked/charging the robot is one more FIXED point (its dock
     doesn't move), on whatever band/SSID your IoT network uses.
  2. Survey log roborock-rf-survey.jsonl — EVERY poll's reading with
     robot state + cleaned area, docked or cleaning. While it
     cleans, this is a spatial RSSI survey in the making: join it
     against the robot's map pose later and you have a per-room
     radio map that calibrates every fixed point.

Discipline: short single-purpose local session (the pattern the
`python-roborock` device-manager API supports cleanly: build the
manager against a cached login, wait for the local channel, one RPC
+ one status refresh, close). Read-only commands only. Never raises
— any failure prints an abstain line and exits 0. Freshness markers
persist on every run (see the Shield leg for why).

Prerequisites (your side of the pattern): a tools directory holding
  - relay.py (or equivalent) that exposes the robot's local port
    58867 on 127.0.0.1 — skip/empty it if your host reaches the
    robot's LAN address directly;
  - local_credentials.json ({"duid": ...}) + userdata.json (cached
    vendor login) for the python-roborock device manager;
  - a venv with python-roborock + aiohttp installed.
These come from the standard Roborock local-lane setup; the sampler
only consumes them.

Usage: python3 roborock_rssi_sampler.py   (idempotent; safe to hook)
"""
import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
SERIES = os.path.join(MESH_DIR, "rssi-series.jsonl")
SURVEY = os.path.join(MESH_DIR, "roborock-rf-survey.jsonl")
STATE_F = os.path.join(MESH_DIR, "roborock-rssi-state.json")
# Directory with relay.py + local_credentials.json + userdata.json +
# venv/ (see docstring).
TOOLS = os.path.expanduser(os.environ.get(
    "ROBOROCK_TOOLS_DIR", os.path.join(MESH_DIR, "roborock-tools")))
# Your Roborock account email + the vendor API base for your region.
ACCOUNT_EMAIL = os.environ.get("ROBOROCK_ACCOUNT_EMAIL", "you@example.com")
BASE_URL = os.environ.get("ROBOROCK_BASE_URL", "https://usiot.roborock.com")
POINT = "roborock-ap-main"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state():
    try:
        with open(STATE_F) as f:
            return json.load(f)
    except Exception:
        return {}


def note_state(**kw):
    st = load_state()
    st.update(kw)
    st["last_poll_utc"] = now_iso()
    with open(STATE_F, "w") as f:
        json.dump(st, f)


async def pull():
    import aiohttp
    from roborock import RoborockCommand
    from roborock.data import NetworkInfo, UserData
    from roborock.devices.cache import CacheData, DeviceCacheData, InMemoryCache
    from roborock.devices.device_manager import UserParams, create_device_manager

    creds = json.load(open(os.path.join(TOOLS, "local_credentials.json")))
    duid = creds["duid"]
    user_data = UserData.from_dict(
        json.load(open(os.path.join(TOOLS, "userdata.json"))))
    cache = InMemoryCache()
    cd = CacheData()
    # The local channel is reached through the relay on 127.0.0.1.
    cd.device_info[duid] = DeviceCacheData(
        network_info=NetworkInfo.from_dict({"ip": "127.0.0.1"}))
    await cache.set(cd)
    async with aiohttp.ClientSession(trust_env=True) as session:
        params = UserParams(username=ACCOUNT_EMAIL,
                            user_data=user_data, base_url=BASE_URL)
        mgr = await asyncio.wait_for(
            create_device_manager(params, session=session, cache=cache),
            timeout=60)
        try:
            dev = (await asyncio.wait_for(mgr.get_devices(), timeout=60))[0]
            for _ in range(30):
                if dev.is_local_connected:
                    break
                await asyncio.sleep(2)
            if not dev.is_local_connected:
                raise RuntimeError("local channel never connected")
            ni = await asyncio.wait_for(
                dev.v1_properties.command.send(
                    RoborockCommand.GET_NETWORK_INFO), timeout=20)
            await asyncio.wait_for(
                dev.v1_properties.status.refresh(), timeout=30)
            st = dev.v1_properties.status.as_dict() or {}
            return ni, st
        finally:
            await mgr.close()


def main() -> int:
    # The roborock package lives in the tools venv; if the poll
    # launched us under a different python, re-exec under it.
    try:
        import roborock  # noqa: F401
    except ImportError:
        venv_py = os.path.join(TOOLS, "venv/bin/python")
        if os.environ.get("RRSSI_REEXEC") != "1" and os.path.exists(venv_py):
            os.environ["RRSSI_REEXEC"] = "1"
            os.execv(venv_py, [venv_py, os.path.abspath(__file__)])
        note_state(last_status="roborock-package-missing")
        print("roborock-rssi: abstain — roborock package unavailable")
        return 0
    relay_py = os.path.join(TOOLS, "relay.py")
    relay = None
    if os.path.exists(relay_py):
        relay = subprocess.Popen(
            [os.path.join(TOOLS, "venv/bin/python"), relay_py],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        try:
            ni, st = asyncio.run(asyncio.wait_for(pull(), timeout=150))
        except Exception as e:
            note_state(last_status=f"pull-failed:{type(e).__name__}")
            print(f"roborock-rssi: abstain — local pull failed "
                  f"({type(e).__name__})")
            return 0
    finally:
        if relay is not None:
            relay.terminate()

    rssi = ni.get("rssi") if isinstance(ni, dict) else None
    if not isinstance(rssi, (int, float)):
        note_state(last_status="no-rssi-in-network-info")
        print("roborock-rssi: abstain — GET_NETWORK_INFO carried no rssi")
        return 0
    state_code = st.get("state")
    clean_area = st.get("square_meter_clean_area") or st.get("clean_area")

    survey_row = {"ts": now_iso(), "rssi": rssi,
                  "ssid": ni.get("ssid"), "bssid": ni.get("bssid"),
                  "freq": ni.get("freq"), "robot_state": state_code,
                  "clean_area_m2": clean_area}
    with open(SURVEY, "a") as f:
        f.write(json.dumps(survey_row) + "\n")

    sig = json.dumps({"rssi": rssi, "state": state_code}, sort_keys=True)
    prev = load_state()
    if prev.get("last_sig") == sig:
        note_state(last_status="ok", last_sample_ts=now_iso(),
                   last_rssi=rssi, last_robot_state=state_code)
        print(f"roborock-rssi: deduped {POINT}={rssi} (state {state_code})")
        return 0
    row = {"ts": now_iso(),
           "source": "roborock GET_NETWORK_INFO (local lane, "
                     "awareness poll)",
           "points": {POINT: rssi}}
    with open(SERIES, "a") as f:
        f.write(json.dumps(row) + "\n")
    note_state(last_status="ok", last_sample_ts=now_iso(),
               last_rssi=rssi, last_robot_state=state_code, last_sig=sig)
    print(f"roborock-rssi: appended {POINT}={rssi} (state {state_code}, "
          f"ssid {ni.get('ssid')}, freq {ni.get('freq')})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
