#!/usr/bin/env python3
"""RSSI baseline learner — per-point, per-hour norms for the series.

Learns, per series point and per local hour, the normal RSSI value
distribution and the normal short-window RANGE, from the accumulated
rssi-series.jsonl. The vote layer (votes/rf_votes.py) consumes the
result: a point is MATURE once it has >= 200 samples across >= 4
distinct days; for mature points the RSSI vote keys on deviation
from this baseline (anomaly — "unusual for this point at this
hour") instead of a raw range threshold, and the range method keeps
serving immature points meanwhile.

Why per-hour: evenings are legitimately wobbly (people, cooking,
streaming) and 3 AM is legitimately flat. A single global threshold
calls every busy evening "activity" and every quiet night "empty";
the baseline separates "unusual" from "normal for now".

Baselines are statistics about the series, nothing more: they never
feed a gate, and a point's baseline says nothing about any person.

Writes rssi-baseline.json next to the series:
  {generated, points: {name: {samples, days, mature,
     hours: {"0".."23": {mean, std, range_mean, range_std, n}}}}}
Range stats come from consecutive 6-sample chunks within each
(day, hour) bucket — comparable to the vote's anomaly check, which
uses the point's last 6 values.

Usage: python3 rssi_baseline.py   (cheap; safe to run every poll)
"""
import json
import math
import os
import sys
from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

# ------------------------------------------------------------------
# CONFIG — adapt these. Every value can also be set by environment.
# ------------------------------------------------------------------
MESH_DIR = os.path.expanduser(os.environ.get(
    "RSSI_MESH_DIR", os.path.join(os.path.dirname(
        os.path.abspath(__file__)), "..")))
SERIES = os.path.join(MESH_DIR, "rssi-series.jsonl")
OUT = os.path.join(MESH_DIR, "rssi-baseline.json")
# Your household timezone — "local hour" is the whole point.
LOCAL_TZ = ZoneInfo(os.environ.get("RSSI_MESH_TZ", "UTC"))
CHUNK = 6
MATURE_SAMPLES = 200
MATURE_DAYS = 4


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def std(xs, m=None):
    if len(xs) < 2:
        return 0.0
    m = mean(xs) if m is None else m
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def main() -> int:
    vals = defaultdict(list)          # (point, hour) -> [values]
    days = defaultdict(set)           # point -> {dates}
    totals = defaultdict(int)         # point -> n
    order = defaultdict(list)         # (point, date, hour) -> [values in order]
    try:
        with open(SERIES) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    ts = datetime.fromisoformat(
                        row["ts"].replace("Z", "+00:00"))
                except Exception:
                    continue
                et = ts.astimezone(LOCAL_TZ)
                for k, v in (row.get("points") or {}).items():
                    if isinstance(v, (int, float)):
                        vals[(k, et.hour)].append(float(v))
                        order[(k, et.date().isoformat(), et.hour)].append(float(v))
                        days[k].add(et.date().isoformat())
                        totals[k] += 1
    except FileNotFoundError:
        pass

    points = {}
    for k in sorted(totals):
        hours = {}
        # range chunks per hour across days
        chunks = defaultdict(list)
        for (pk, _d, h), vs in order.items():
            if pk != k:
                continue
            for i in range(0, len(vs) - CHUNK + 1, CHUNK):
                c = vs[i:i + CHUNK]
                chunks[h].append(max(c) - min(c))
        for h in range(24):
            vs = vals.get((k, h), [])
            if len(vs) < 8:
                continue
            cs = chunks.get(h, [])
            hours[str(h)] = {
                "mean": round(mean(vs), 2),
                "std": round(std(vs), 2),
                "range_mean": round(mean(cs), 2) if cs else None,
                "range_std": round(std(cs), 2) if cs else None,
                "n": len(vs),
            }
        points[k] = {
            "samples": totals[k],
            "days": len(days[k]),
            "mature": bool(totals[k] >= MATURE_SAMPLES
                           and len(days[k]) >= MATURE_DAYS),
            "hours": hours,
        }
    out = {"generated": datetime.now(LOCAL_TZ).astimezone().isoformat(),
           "maturity_rule": f">={MATURE_SAMPLES} samples across "
                            f">={MATURE_DAYS} days",
           "points": points}
    write_json(OUT, out)
    summ = ", ".join(
        f"{k}: {v['samples']} samples/{v['days']}d"
        f"{' MATURE' if v['mature'] else ''}"
        for k, v in points.items()) or "no series yet"
    print(f"rssi-baseline: {summ}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
