#!/usr/bin/env bash
# automation_engine_poll.sh — programmatic poller for the smart-home automation layer.
# Runs the declarative automation engine; the engine does 100% of the work
# (entity refresh, trigger/condition evaluation, service execution).
# The agent wakes ONLY on engine failure or when a speaker broadcast fired
# (so the worker can post the chat record). Everything else stays silent.
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

ENGINE="${SMART_HOME_ENGINE:-$HOME/workspace/goals/manage-my-smart-home/bin/automation_engine.py}"

args=(--hook)
if [ "${HATCH_HOOK_DRY_RUN:-0}" = "1" ]; then
  args+=(--dry-run)
fi

if ! out="$(python3 "$ENGINE" "${args[@]}" 2>/dev/null)"; then
  log "engine exec failed" '{}'
  wake "automation-engine-error" '{"error":"engine process failed"}'
fi

# shellcheck disable=SC2154
eval "$(python3 - "$out" <<'EOF'
import json, sys
try:
    d = json.loads(sys.argv[1])
except Exception as e:
    print(f'RESULT=badjson')
    sys.exit()
fired = d.get("fired", [])
errors = [e for f in fired for e in f.get("errors", [])]
broadcasts = sum(f.get("broadcasts", 0) for f in fired)
aliases = [f["alias"] for f in fired]
def sh(v): return "'" + v.replace("'", "'\"'\"'") + "'"
print(f"RESULT=ok")
print(f"FIRED_COUNT={len(fired)}")
print(f"ERROR_COUNT={len(errors)}")
print(f"BROADCAST_COUNT={broadcasts}")
print(f"ALIASES={sh(','.join(aliases))}")
print(f"ERRORS={sh('; '.join(errors[:5]))}")
print(f"PAYLOAD={sh(sys.argv[1][:2000])}")
EOF
)"

if [ "${RESULT:-}" = "badjson" ]; then
  log "engine returned unparseable output" '{}'
  wake "automation-engine-error" '{"error":"unparseable engine output"}'
  exit 0
fi

if [ "${ERROR_COUNT:-0}" -gt 0 ]; then
  log "engine errors" "{\"errors\":\"$ERRORS\"}"
  wake "automation-engine-error" "{\"errors\":\"$ERRORS\",\"fired\":\"$ALIASES\"}"
  exit 0
fi

if [ "${BROADCAST_COUNT:-0}" -gt 0 ]; then
  wake "automation-broadcast-sent" "{\"fired\":\"$ALIASES\",\"detail\":$PAYLOAD}"
  exit 0
fi

if [ "${FIRED_COUNT:-0}" -gt 0 ]; then
  silent "automations fired programmatically" "{\"fired\":\"$ALIASES\"}"
  exit 0
fi

silent "no automations due" '{}'
