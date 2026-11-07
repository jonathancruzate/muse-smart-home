#!/usr/bin/env python3
"""Android TV scan-RSSI sampler — a fixed point for the RSSI series
(pattern leg 2 of 4).

The Ethernet trick: a wired Android TV box (the reference install uses
an NVIDIA Shield TV) has no associated Wi-Fi link to read RSSI from —
but its Wi-Fi radio still SCANS, and scan results carry a per-AP RSSI
for every access point it can hear. A tiny telemetry app on the box
(the companion `shield-wifi-sense` project) samples at 2 Hz to an
on-device JSONL; this sampler pulls the newest sample over ADB and
appends one row per distinct scan to rssi-series.jsonl.

Each run (rides a 300 s poll, alongside the other legs):
  1. ensures the ADB lane — on any failure it prints an abstain line
     and exits 0; a poll leg must never break the poll;
  2. ensures the telemetry app is running (relaunches it if not — it
     is your own app, installed for exactly this);
  3. appends ONE row with one point per configured AP (scan RSSI by
     BSSID), deduped on the scan signature so repeated OS-cached
     scans don't fabricate samples;
  4. truncates the on-device JSONL past ~3 MB (the app appends per
     sample, so truncation is safe; at 2 Hz the file otherwise grows
     ~175 MB/day; app versions >= 1.1 also rotate in-app).

Fresh rows only: a newest sample older than 12 min abstains — a dead
app is a defect to see in the printout, never a stale fact. Freshness
markers (last_poll_utc / last_sample_ts / last_status) are persisted
on EVERY run: dedupe means a quiet room legitimately appends no rows,
so row-absence can't distinguish 'flat' from 'dead' — the markers can.

Signal class: scan-RSSI variance at a fixed point — coarse presence /
motion corroboration only. Never who, never where exactly, never
pose or vitals.

Usage: python3 shield_rssi_sampler.py   (idempotent; safe to hook)
"""
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
OUT = os.path.join(MESH_DIR, "rssi-series.jsonl")
STATE_F = os.path.join(MESH_DIR, "shield-rssi-state.json")
ADB = os.path.expanduser(os.environ.get("ADB_BIN", "adb"))
# Your TV box's ADB endpoint — direct LAN ADB, or a forwarded port if
# you reach the box through a tunnel (the reference install forwards
# the box's ADB port to a local port; use whatever `adb connect`
# target works for you).
SERIAL = os.environ.get("SHIELD_ADB_SERIAL", "android-tv.local:5555")
# Your telemetry app's package id (companion shield-wifi-sense app).
PKG = os.environ.get("SHIELD_SENSE_PKG", "com.example.shieldwifisense")
REMOTE = f"/sdcard/Android/data/{PKG}/files/shield-wifi-sense.jsonl"
# Your access points: map each AP's BSSID to the point name you want
# in the series. The two below are placeholders — replace them.
APS = {"aa:bb:cc:dd:ee:01": "shield-ap-main",
       "aa:bb:cc:dd:ee:02": "shield-ap-extender"}
TRUNCATE_BYTES = 3_000_000
FRESH_SECS = 720


def adb(args, timeout=20):
    r = subprocess.run([ADB, "-s", SERIAL] + args,
                       capture_output=True, text=True, timeout=timeout)
    return r.stdout or ""


def ensure_lane():
    out = adb(["get-state"], timeout=10).strip()
    if out == "device":
        return True
    subprocess.run([ADB, "connect", SERIAL],
                   capture_output=True, text=True, timeout=15)
    return adb(["get-state"], timeout=10).strip() == "device"


def load_state():
    try:
        with open(STATE_F) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    with open(STATE_F, "w") as f:
        json.dump(st, f)


def note_state(**kw):
    """Persist freshness markers on EVERY run (a watchdog reads them:
    dedupe means a quiet room legitimately appends no rows, so
    row-absence can't distinguish 'flat' from 'dead' —
    last_poll_utc / last_sample_ts / last_status can)."""
    st = load_state()
    st.update(kw)
    st["last_poll_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(st)


def main() -> int:
    try:
        if not ensure_lane():
            note_state(last_status="adb-unavailable")
            print("shield-rssi: abstain — ADB lane to TV box unavailable")
            return 0

        if not adb(["shell", "pidof", PKG], timeout=10).strip():
            adb(["shell", "am", "start", "-n",
                 f"{PKG}/.MainActivity"], timeout=15)
            print("shield-rssi: app was not running — relaunched")

        size_s = adb(["shell", "stat", "-c", "%s", REMOTE],
                     timeout=10).strip()
        size = int(size_s) if size_s.isdigit() else 0

        tail = adb(["shell", "tail", "-c", "6000", REMOTE], timeout=15)
        sample = None
        for line in tail.strip().splitlines():
            try:
                sample = json.loads(line)
            except Exception:
                continue
        if not sample:
            note_state(last_status="no-parseable-sample")
            print("shield-rssi: abstain — no parseable sample in app log")
            return 0

        ts = sample.get("iso_ts", "")
        try:
            when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - when).total_seconds()
        except Exception:
            age = None
        if age is None or age > FRESH_SECS:
            note_state(last_status="sample-stale", last_sample_ts=ts)
            print(f"shield-rssi: abstain — newest sample stale "
                  f"(age {age if age is None else int(age)}s, ts {ts})")
            return 0

        points = {}
        # Newer apps log the full scan vector in `scan_aps`; older
        # ones logged only `strongest_5_aps`. Prefer the full vector.
        scan_list = sample.get("scan_aps") or sample.get("strongest_5_aps", [])
        for ap in scan_list:
            key = APS.get(str(ap.get("bssid", "")).lower())
            if key and isinstance(ap.get("rssi"), (int, float)):
                points[key] = int(ap["rssi"])
        if not points:
            note_state(last_status="home-aps-absent", last_sample_ts=ts)
            print("shield-rssi: abstain — home APs absent from scan results")
            return 0

        sig = json.dumps(points, sort_keys=True)
        st = load_state()
        appended = False
        if st.get("last_sig") != sig:
            row = {"ts": ts,
                   "source": "shield-wifi-sense scan (adb, awareness poll)",
                   "points": points}
            with open(OUT, "a") as f:
                f.write(json.dumps(row) + "\n")
            st["last_sig"] = sig
            appended = True

        truncated = False
        if size > TRUNCATE_BYTES:
            adb(["shell", "truncate", "-s", "0", REMOTE], timeout=10)
            truncated = True
        st["last_status"] = "ok"
        st["last_sample_ts"] = ts
        st["last_poll_utc"] = datetime.now(timezone.utc).isoformat()
        save_state(st)

        print(f"shield-rssi: {'appended' if appended else 'deduped'} "
              f"{points} (scan {sample.get('scan_result_count')} APs, "
              f"sample age {int(age)}s"
              f"{', truncated app log at ' + str(size) + 'B' if truncated else ''})")
        return 0
    except Exception as e:  # a poll leg never breaks the poll
        print(f"shield-rssi: abstain — sampler error: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
