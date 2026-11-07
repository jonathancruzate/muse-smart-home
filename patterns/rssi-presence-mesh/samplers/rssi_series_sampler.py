#!/usr/bin/env python3
"""Read-only RSSI time-series sampler for fixed Wi-Fi points whose
vendor feed already reports an AP-link RSSI (pattern leg 1 of 4).

Piggybacks on an EXISTING vendor cloud feed — in the reference home a
pair of air purifiers whose 300 s awareness feed writes a state file
with each device's AP-link RSSI. This sampler makes NO cloud calls of
its own: each run reads the feed's state file and appends a sample to
rssi-series.jsonl when the feed timestamp has advanced.

Why: per-scan RSSI variance at fixed receivers is the coarsest RF
body-sensing signal short of CSI — the rung Wi-Fi-sensing projects
call coarse-RSSI mode. Any always-on device that reports its AP-link
RSSI (purifiers, plugs, appliances) is a fixed point waiting to be
used. This sampler just stops throwing the series away.

Feed contract (adapt to your vendor): a JSON state file shaped like
  {"ts": <iso>, "devices": {"<point-name>": {"rssi": <-42>, ...}}}
Point names come from the feed's own device keys.

Usage: python3 rssi_series_sampler.py   (idempotent; safe to cron/hook)
"""
import json
import os
import sys

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
FEED_STATE = os.path.expanduser(os.environ.get(
    "RSSI_FEED_STATE", os.path.join(MESH_DIR, "vendor-cloud-state.json")))
OUT = os.path.join(MESH_DIR, "rssi-series.jsonl")
SOURCE_TAG = "vendor-cloud-state.json (existing 300s awareness feed)"


def main() -> int:
    with open(FEED_STATE) as f:
        snap = json.load(f)
    ts = snap.get("ts")
    devs = snap.get("devices", {})
    sample = {
        "ts": ts,
        "source": SOURCE_TAG,
        "points": {
            k: (int(v["rssi"]) if str(v.get("rssi", "")).lstrip("-").isdigit() else None)
            for k, v in devs.items()
            if "rssi" in v
        },
    }
    # Dedupe against the last row FROM THIS SOURCE specifically — the
    # series is interleaved with the other samplers' rows, so the
    # file's last line is no longer necessarily this sampler's own.
    last_ts = None
    if os.path.exists(OUT):
        with open(OUT) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if str(row.get("source", "")).startswith("vendor-cloud"):
                    last_ts = row.get("ts")
    if ts and ts != last_ts and sample["points"]:
        with open(OUT, "a") as f:
            f.write(json.dumps(sample) + "\n")
        print(f"rssi-sampler: appended {ts} {sample['points']}")
    else:
        print(f"rssi-sampler: no new feed snapshot (last={last_ts})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
