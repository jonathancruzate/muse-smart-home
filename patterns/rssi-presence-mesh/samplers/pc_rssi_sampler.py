#!/usr/bin/env python3
"""Windows PC scan-RSSI sampler — the Ethernet trick at the desk
(pattern leg 4 of 4).

The PC is Ethernet-connected, but its Wi-Fi adapter can still scan:
`netsh wlan show networks mode=bssid` over an SSH lane yields the
desk-area view of your home APs -> series points `pc-ap-main` /
`pc-ap-extender`. Windows reports signal as a percentage; dBm uses
the standard approximation dBm = pct/2 - 100.

Desktops sleep. The sampler abstains quietly (lane down = no fact,
never an error storm), dedupes on the scan signature, and stamps
freshness markers in pc-rssi-state.json on every run. NOTE: judge
this leg on its poll markers, not on sample age — a sleeping PC is
the normal state whenever nobody is home.

Reaching the PC: if yours is directly SSH-able, just set
PC_SSH_HOST. If it sits behind a mesh VPN with no inbound route from
your automation host, set PC_SSH_FORWARD_CMD to whatever command
establishes a local port forward (the reference install uses a small
tailnet forwarder); the sampler runs it when the direct probe fails.

Usage: python3 pc_rssi_sampler.py   (idempotent; safe to hook)
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
SERIES = os.path.join(MESH_DIR, "rssi-series.jsonl")
STATE_F = os.path.join(MESH_DIR, "pc-rssi-state.json")
SSH_HOST = os.environ.get("PC_SSH_HOST", "127.0.0.1")
SSH_PORT = os.environ.get("PC_SSH_PORT", "2222")
SSH_USER = os.environ.get("PC_SSH_USER", "you")
SSH_KEY = os.path.expanduser(os.environ.get(
    "PC_SSH_KEY", "~/.ssh/id_ed25519"))
# Optional: shell command that establishes a local forward to the
# PC's SSH port (leave empty for a direct connection). Example shape:
#   ~/bin/mesh-forward.py 2222 <pc-mesh-address>:22
FORWARD_CMD = os.environ.get("PC_SSH_FORWARD_CMD", "")
# Your access points: BSSID -> point name. Placeholders — replace.
APS = {"aa:bb:cc:dd:ee:01": "pc-ap-main",
       "aa:bb:cc:dd:ee:02": "pc-ap-extender"}


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


def ssh(cmd, timeout=25):
    p = subprocess.run(
        ["ssh", "-p", SSH_PORT, "-i", SSH_KEY, "-o", "ConnectTimeout=10",
         "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         f"{SSH_USER}@{SSH_HOST}", cmd],
        capture_output=True, text=True, timeout=timeout)
    return p.stdout if p.returncode == 0 else ""


def ensure_lane():
    if ssh("echo ok", timeout=15).strip() == "ok":
        return True
    if not FORWARD_CMD:
        return False
    subprocess.Popen(FORWARD_CMD, shell=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(5)
    return ssh("echo ok", timeout=15).strip() == "ok"


def parse_scan(text):
    """netsh wlan show networks mode=bssid -> {bssid_lower: signal_pct}"""
    out = {}
    cur = None
    for line in text.splitlines():
        m = re.search(r"BSSID\s*\d*\s*:\s*([0-9a-fA-F:]{17})", line)
        if m:
            cur = m.group(1).lower()
            continue
        m = re.search(r"Signal\s*:\s*(\d+)%", line)
        if m and cur:
            out[cur] = int(m.group(1))
            cur = None
    return out


def pct_to_dbm(pct):
    return int(round(pct / 2.0 - 100))


def main() -> int:
    try:
        if not ensure_lane():
            note_state(last_status="pc-unreachable")
            print("pc-rssi: abstain — PC SSH lane unavailable (asleep/off)")
            return 0
        text = ssh("netsh wlan show networks mode=bssid", timeout=40)
        if not text:
            note_state(last_status="netsh-empty")
            print("pc-rssi: abstain — netsh returned nothing (Wi-Fi "
                  "adapter off or scan failed)")
            return 0
        scan = parse_scan(text)
        points = {APS[b]: pct_to_dbm(p) for b, p in scan.items() if b in APS}
        if not points:
            note_state(last_status="home-aps-absent",
                       last_scan_aps=len(scan))
            print(f"pc-rssi: abstain — home APs absent from PC scan "
                  f"({len(scan)} BSSIDs seen)")
            return 0
        sig = json.dumps(points, sort_keys=True)
        if load_state().get("last_sig") == sig:
            note_state(last_status="ok", last_sample_ts=now_iso())
            print(f"pc-rssi: deduped {points}")
            return 0
        row = {"ts": now_iso(),
               "source": "pc netsh wlan scan (ssh, awareness poll)",
               "points": points}
        with open(SERIES, "a") as f:
            f.write(json.dumps(row) + "\n")
        note_state(last_status="ok", last_sample_ts=now_iso(), last_sig=sig)
        print(f"pc-rssi: appended {points} (scan saw {len(scan)} BSSIDs)")
        return 0
    except Exception as e:  # sampler NEVER fails the poll
        note_state(last_status=f"error:{type(e).__name__}")
        print(f"pc-rssi: abstain — sampler error {type(e).__name__}: {e}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
