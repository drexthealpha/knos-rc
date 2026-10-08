#!/bin/bash
# staging harness (A 0.3.21): knos proof judge --sandbox hermetic on one tree, with uid 65534's task count sampled
set -uo pipefail
d=$1; issue=$2; peak=$RUNNER_TEMP/peak-$(basename "$d")
( m=0; while :; do n=$(ps -L -u 65534 --no-headers 2>/dev/null | wc -l); [ "$n" -gt "$m" ] && m=$n && echo "$m" > "$peak"; sleep 0.05; done ) &
s=$!
cd "$d"
knos proof judge --base base --pr pr --issue "$issue" --changed changed.txt --sandbox hermetic --evidence ev.json
code=$?
kill "$s" 2>/dev/null
"$(uv tool dir)/knos/bin/python" - "$d/ev.json" <<'PY' || true
import json, sys
ev = json.load(open(sys.argv[1])).get("evidence", {})
print("evidence:", json.dumps({k: ev.get(k) for k in ("assurance", "runner", "sandbox", "image", "limits", "host_limits", "fallback") if k in ev}))
PY
echo "$(basename "$d"): knos proof judge exit $code; most tasks of uid 65534 at once: $(cat "$peak" 2>/dev/null || echo 0) (limit 256)" | tee -a "$GITHUB_STEP_SUMMARY"
exit $code
