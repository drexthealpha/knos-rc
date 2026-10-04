#!/usr/bin/env bash
# Execute the proposed upgrades when their 48 hours have passed, unattended.
#
# `bash scripts/deploy_v2.sh --propose` wrote <key folder>/upgrade-schedule.json: each proposal's index and the time
# from which all of them can be executed, plus ten minutes (run_at). This script arranges for one run at that time:
#
#     node scripts/governance.mjs upgrade execute <index>      for each proposal, in the order of their indexes
#     knos status                                              what is on chain afterwards
#
# with every line of both appended to <key folder>/upgrade-run.log.
#
#   bash scripts/schedule_upgrade.sh              arrange the run, and print how to cancel it
#   bash scripts/schedule_upgrade.sh --show       what is arranged, and what the log says so far
#   bash scripts/schedule_upgrade.sh --cancel     take the arrangement back (the proposals stay as they are on chain)
#   bash scripts/schedule_upgrade.sh --run        the run itself, now: what the timer calls. Safe by hand too: a
#                                                 proposal whose time has not come is refused by governance.mjs and by
#                                                 the Squads program, and one already executed is left alone
#   --with systemd|at|schtasks                    choose the timer instead of taking the first that works here
#
# The timer is the first of these that works on this machine:
#   systemd   systemd-run --user --on-calendar: a transient timer of the user's systemd (unit knos-upgrade)
#   at        the at command, when its daemon (atd) is running
#   schtasks  a Windows scheduled task (KnosUpgrade), made with schtasks.exe from inside WSL, that starts
#             `wsl.exe -d <this distribution>` at the time: it runs even if no WSL window is open then
# Under WSL the first two fire only while the distribution is running at that time (a WSL with no open window stops
# after a few seconds), and the script says so: keep a window open, or pass --with schtasks.
#
# Running it again replaces the arrangement with one for the time the schedule file names now, so it is safe after a
# second --propose. Nothing here sends a transaction before --run.
#
# What it reads
#   KNOS_KEYS, KNOS_FEE_PAYER, KNOS_MEMBERS, KNOS_RPC    as scripts/governance.mjs reads them. They, and PATH, are
#                      written to <key folder>/upgrade-run.env (mode 600) so that the run has what this shell has
#   KNOS_SCHEDULE      the schedule file (default: upgrade-schedule.json in the key folder)
#   KNOS_WSL_DISTRO    schtasks: the distribution wsl.exe starts (default: $WSL_DISTRO_NAME, else Ubuntu-24.04)
#
# Needs: node 20 or later with `npm ci --prefix scripts` done, `knos` on PATH (pip install -e . or pipx install knos),
# and one of the three timers.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD

ACTION=schedule WITH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --show) ACTION=show ;;
    --cancel) ACTION=cancel ;;
    --run) ACTION=run ;;
    --with) shift; WITH="${1:-}"; case "$WITH" in systemd|at|schtasks) ;; *) echo "--with takes systemd, at or schtasks" >&2; exit 2 ;; esac ;;
    -h|--help) awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); print; next } NR > 1 { exit }' "$0"; exit 0 ;;
    *) echo "usage: bash scripts/schedule_upgrade.sh [--show | --cancel | --run] [--with systemd|at|schtasks]" >&2; exit 2 ;;
  esac
  shift
done

KEYS="${KNOS_KEYS:-$ROOT/.knos-keys}"
SCHEDULE="${KNOS_SCHEDULE:-$KEYS/upgrade-schedule.json}"
LOG="$KEYS/upgrade-run.log"
ENVFILE="$KEYS/upgrade-run.env"
STATE="$KEYS/upgrade-run.timer"            # how the run was arranged: the timer's kind and its name or job number
UNIT=knos-upgrade
TASK=KnosUpgrade

die() { echo "stopped: $*" >&2; exit 1; }
field() { node -e 'const s = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")); const v = process.argv[2] === "indexes" ? s.proposals.map((p) => p.index).join(" ") : s[process.argv[2]]; if (v === undefined || v === null || v === "") process.exit(1); console.log(v);' "$SCHEDULE" "$1"; }
wsl() { [ -n "${WSL_DISTRO_NAME:-}" ] || grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; }
utc() { date -u -d "@$1" "+%Y-%m-%d %H:%M:%S UTC"; }

# the run is started by a timer, with a bare environment: what the arranging shell had comes first
# shellcheck disable=SC1090  # written by this script when the run was arranged
if [ "$ACTION" = run ] && [ -f "$ENVFILE" ]; then . "$ENVFILE"; fi
command -v node >/dev/null 2>&1 || die "node is not on PATH. Install Node 20 or later."

# ---- the run itself ---------------------------------------------------------------------------------------------------
run() {
  local index failed=0 rpc
  [ -f "$SCHEDULE" ] || die "$SCHEDULE is missing: nothing was proposed, or the key folder is another one (KNOS_KEYS)."
  rpc="$(field rpc)" || die "$SCHEDULE names no cluster."
  mkdir -p "$(dirname "$LOG")"
  {
    echo "==== $(date -u "+%Y-%m-%d %H:%M:%S UTC")  the scheduled upgrade run starts (cluster $rpc; proposals $(field indexes))"
    for index in $(field indexes); do
      echo "---- node scripts/governance.mjs upgrade execute $index"
      if KNOS_KEYS="$KEYS" node "$ROOT/scripts/governance.mjs" upgrade execute "$index" --rpc "$rpc"; then
        echo "---- proposal $index: executed (or executed before)"
      else
        echo "---- proposal $index: NOT executed (the lines above say why)"; failed=$((failed + 1))
      fi
    done
    echo "---- knos status"
    if command -v knos >/dev/null 2>&1; then KNOS_RPC="$rpc" knos status || echo "---- knos status found something to look at (the lines above)"
    else echo "---- knos is not on PATH: run it by hand: KNOS_RPC=$rpc knos status"; fi
    if [ "$failed" = 0 ]; then echo "==== $(date -u "+%Y-%m-%d %H:%M:%S UTC")  done: every proposal is executed"
    else echo "==== $(date -u "+%Y-%m-%d %H:%M:%S UTC")  done: $failed proposal(s) NOT executed. Run it again by hand: bash scripts/schedule_upgrade.sh --run"; fi
  } >> "$LOG" 2>&1
  tail -n 3 "$LOG"
  [ "$failed" = 0 ]
}

# ---- taking an arrangement back --------------------------------------------------------------------------------------
cancel() {
  local kind id
  if [ ! -f "$STATE" ]; then echo "nothing is arranged (no $STATE)"; return 0; fi
  read -r kind id < "$STATE"
  case "$kind" in
    systemd) systemctl --user stop "$id.timer" >/dev/null 2>&1 || true; systemctl --user reset-failed "$id.timer" "$id.service" >/dev/null 2>&1 || true ;;
    at) atrm "$id" >/dev/null 2>&1 || true ;;
    schtasks) schtasks.exe /Delete /TN "$id" /F >/dev/null 2>&1 || true ;;
    *) die "$STATE names a timer this script does not know ($kind)." ;;
  esac
  rm -f "${STATE:?}"
  echo "cancelled: the $kind timer $id is gone. The proposals are unchanged on chain; node scripts/governance.mjs cancel upgrade <index> withdraws one."
}

# ---- which timer works here -------------------------------------------------------------------------------------------
works() {
  case "$1" in
    systemd) command -v systemd-run >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1 ;;
    at) command -v at >/dev/null 2>&1 && { pgrep -x atd >/dev/null 2>&1 || systemctl is-active --quiet atd 2>/dev/null; } ;;
    schtasks) command -v schtasks.exe >/dev/null 2>&1 && command -v wslpath >/dev/null 2>&1 ;;
  esac
}

arrange() {
  local at kind="" k command id xml distro
  [ -f "$SCHEDULE" ] || die "$SCHEDULE is missing. Propose the upgrades first: bash scripts/deploy_v2.sh --propose"
  at="$(field run_at)" || die "$SCHEDULE names no time (run_at): propose the upgrades again."
  field indexes >/dev/null || die "$SCHEDULE names no proposal."
  [ "$at" -gt "$(date +%s)" ] || die "the time in $SCHEDULE ($(utc "$at")) has passed already. Run it now: bash scripts/schedule_upgrade.sh --run"
  for k in ${WITH:-systemd at schtasks}; do
    if works "$k"; then kind="$k"; break; fi
  done
  [ -n "$kind" ] || die "no timer works here${WITH:+ (--with $WITH was asked)}: systemd's user manager does not answer, atd is not running, and schtasks.exe is not on PATH. Under WSL: add [boot] systemd=true to /etc/wsl.conf and restart WSL, or install at (sudo apt install at), or run this from a WSL whose PATH has Windows' System32. Or run it by hand after $(utc "$at"): bash scripts/schedule_upgrade.sh --run"
  [ ! -f "$STATE" ] || cancel | sed 's/^/replacing the earlier arrangement: /'
  # what the run needs of this shell, kept beside the keys: where node and knos are, and which keys and cluster
  ( umask 077
    { printf 'export PATH=%q\n' "$PATH"
      for k in KNOS_KEYS KNOS_FEE_PAYER KNOS_MEMBERS KNOS_SCHEDULE; do
        if [ -n "${!k:-}" ]; then printf 'export %s=%q\n' "$k" "${!k}"; fi
      done; } > "$ENVFILE" )
  command="$(printf '%q' "$ROOT/scripts/schedule_upgrade.sh")"
  case "$kind" in
    systemd)
      id="$UNIT"
      systemd-run --user --unit "$id" --description "Knos: execute the proposed upgrades" --on-calendar "$(date -u -d "@$at" "+%Y-%m-%d %H:%M:%S UTC")" \
        --timer-property=AccuracySec=1s --timer-property=Persistent=true --setenv=KNOS_KEYS="$KEYS" /bin/bash "$ROOT/scripts/schedule_upgrade.sh" --run >/dev/null \
        || die "systemd-run did not make the timer. Try another: --with at, or --with schtasks."
      echo "$kind $id" > "$STATE"
      echo "arranged with systemd: the user timer $id.timer runs the upgrade at $(utc "$at")."
      echo "  see it:    systemctl --user list-timers $id.timer"
      echo "  cancel it: bash scripts/schedule_upgrade.sh --cancel     (or: systemctl --user stop $id.timer)" ;;
    at)
      id="$(echo "KNOS_KEYS=$(printf '%q' "$KEYS") /bin/bash $command --run" | at -t "$(date -d "@$at" "+%Y%m%d%H%M.%S")" 2>&1 | sed -n 's/^job \([0-9][0-9]*\) .*/\1/p' | tail -1)"
      [ -n "$id" ] || die "at did not take the job. Try another: --with systemd, or --with schtasks."
      echo "$kind $id" > "$STATE"
      echo "arranged with at: job $id runs the upgrade at $(utc "$at")."
      echo "  see it:    atq"
      echo "  cancel it: bash scripts/schedule_upgrade.sh --cancel     (or: atrm $id)" ;;
    schtasks)
      id="$TASK" distro="${KNOS_WSL_DISTRO:-${WSL_DISTRO_NAME:-Ubuntu-24.04}}" xml="$KEYS/upgrade-run.task.xml"
      # an XML task, because its start time is written as a UTC instant: `schtasks /ST` reads the time in the Windows
      # user's locale and time zone, which this script cannot know. UTF-16 with a mark, as the Task Scheduler wants it
      { printf '<?xml version="1.0" encoding="UTF-16"?>\n<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        printf '  <RegistrationInfo><Description>Knos: execute the proposed upgrades, then knos status</Description></RegistrationInfo>\n'
        printf '  <Triggers><TimeTrigger><StartBoundary>%s</StartBoundary><Enabled>true</Enabled></TimeTrigger></Triggers>\n' "$(date -u -d "@$at" "+%Y-%m-%dT%H:%M:%SZ")"
        printf '  <Settings><StartWhenAvailable>true</StartWhenAvailable><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>'
        printf '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><WakeToRun>true</WakeToRun><ExecutionTimeLimit>PT1H</ExecutionTimeLimit></Settings>\n'
        printf '  <Actions><Exec><Command>wsl.exe</Command><Arguments>-d %s -- env KNOS_KEYS=%s /bin/bash %s --run</Arguments></Exec></Actions>\n</Task>\n' \
          "$distro" "$(printf '%q' "$KEYS" | sed 's/&/\&amp;/g; s/</\&lt;/g')" "$(printf '%s' "$command" | sed 's/&/\&amp;/g; s/</\&lt;/g')"
      } | { printf '\xff\xfe'; iconv -f UTF-8 -t UTF-16LE; } > "$xml"
      schtasks.exe /Create /TN "$id" /XML "$(wslpath -w "$xml")" /F >/dev/null || die "schtasks.exe did not make the task (the lines above say why)."
      echo "$kind $id" > "$STATE"
      echo "arranged with the Windows Task Scheduler: the task $id starts wsl.exe -d $distro at $(utc "$at"), whether or not a WSL window is open."
      echo "  see it:    schtasks.exe /Query /TN $id /V /FO LIST"
      echo "  cancel it: bash scripts/schedule_upgrade.sh --cancel     (or: schtasks.exe /Delete /TN $id /F)" ;;
  esac
  if [ "$kind" != schtasks ] && wsl; then
    echo "NOTE: this is WSL. A $kind timer fires only if this distribution is running at that time: keep a WSL window open until then,"
    echo "      or arrange it with Windows instead: bash scripts/schedule_upgrade.sh --with schtasks"
  fi
  echo "It will run, for proposals $(field indexes) on $(field rpc): node scripts/governance.mjs upgrade execute <index>, then knos status."
  echo "The log: $LOG     (the upgrades can be executed from $(utc "$(field executable_from)"); the run is ten minutes later)"
}

show() {
  if [ -f "$SCHEDULE" ]; then echo "schedule: proposals $(field indexes) on $(field rpc), executable from $(utc "$(field executable_from)"), run at $(utc "$(field run_at)")"
  else echo "schedule: none ($SCHEDULE does not exist)"; fi
  if [ -f "$STATE" ]; then echo "timer: $(cat "$STATE")"; else echo "timer: none arranged"; fi
  if [ -f "$LOG" ]; then echo "log ($LOG), its last lines:"; tail -n 12 "$LOG" | sed 's/^/  /'; else echo "log: nothing has run yet"; fi
}

case "$ACTION" in
  run) run ;;
  cancel) cancel ;;
  show) show ;;
  schedule) arrange ;;
esac
