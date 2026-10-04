"""scripts/schedule_upgrade.sh with stand-ins for the three timers, node and knos: it arranges ONE run at the time the
schedule file names, with the first timer that works on the machine, says how to cancel it, and the run itself executes
each proposal, then `knos status`, with every line in the log. Nothing here reaches a cluster or a real timer."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "schedule_upgrade.sh"
pytestmark = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash") or not shutil.which("node"), reason="runs the script with bash and node")

STANDINS = {
    "systemd-run": 'echo "systemd-run $*" >> "$CALLS"\n[ "$SYSTEMD" = 1 ]\n',
    "systemctl": 'echo "systemctl $*" >> "$CALLS"\ncase "$*" in "--user show-environment") [ "$SYSTEMD" = 1 ];; "is-active --quiet atd") [ "$ATD" = 1 ];; *) exit 0;; esac\n',
    "pgrep": 'exit 1\n',
    "at": 'echo "at $* <<< $(cat)" >> "$CALLS"\necho "warning: commands will be executed using /bin/sh" >&2\necho "job 17 at Tue Oct  6 01:33:00 2026" >&2\n',
    "atrm": 'echo "atrm $*" >> "$CALLS"\n',
    "schtasks.exe": 'echo "schtasks.exe $*" >> "$CALLS"\n',
    "wslpath": 'echo "C:\\\\keys\\\\$(basename "$2")"\n',
    "knos": 'echo "knos $* rpc=$KNOS_RPC" >> "$CALLS"\necho "12 of 12 checks pass"\n',
}
NODE = ('#!/bin/sh\ncase "$1" in */governance.mjs) echo "node governance $2 $3 $4 $5 $6 keys=$KNOS_KEYS" >> "$CALLS"\n'
        '  if [ "$4" = "$FAIL_INDEX" ]; then echo "refused: its time lock ends later" >&2; exit 1; fi\n'
        '  echo "on chain now: proposal $4 of the upgrade multisig is executed"; exit 0;; esac\nexec {node} "$@"\n')


@pytest.fixture()
def box(tmp_path):
    """A key folder with a schedule, and a PATH that holds the stand-ins before everything else."""
    keys, bin_dir = tmp_path / "keys", tmp_path / "bin"
    keys.mkdir(), bin_dir.mkdir()
    for name, body in STANDINS.items():
        (bin_dir / name).write_text("#!/bin/sh\n" + body, encoding="utf-8")
    (bin_dir / "node").write_text(NODE.replace("{node}", shutil.which("node")), encoding="utf-8")
    for f in bin_dir.iterdir():
        f.chmod(0o755)
    at = int(time.time()) + 172_800
    plan = {"rpc": "https://api.devnet.solana.com", "executable_from": at, "run_at": at + 600,
            "proposals": [{"program": "knos_oidc", "index": 3}, {"program": "knos_pay", "index": 4}]}
    (keys / "upgrade-schedule.json").write_text(json.dumps(plan), encoding="utf-8")
    calls = tmp_path / "calls"
    calls.write_text("", encoding="utf-8")
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "KNOS_KEYS": str(keys), "CALLS": str(calls), "SYSTEMD": "0", "ATD": "0", "HOME": str(tmp_path), "TZ": "UTC"}

    def run(*args: str, **more: str):
        return subprocess.run(["bash", str(SCRIPT), *args], env={**env, **more}, capture_output=True, text=True)
    return keys, calls, plan, run, bin_dir


def _calls(calls: Path) -> list[str]:
    return calls.read_text(encoding="utf-8").splitlines()


def test_systemd_is_taken_first_and_the_timer_is_for_the_schedules_time_in_utc(box):
    keys, calls, plan, run, _ = box
    done = run(SYSTEMD="1", ATD="1")
    assert done.returncode == 0, done.stderr
    [made] = [c for c in _calls(calls) if c.startswith("systemd-run")]
    stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(plan["run_at"]))
    assert f"--user --unit knos-upgrade" in made and f"--on-calendar {stamp}" in made and made.endswith(f"/bin/bash {SCRIPT} --run")
    assert f"arranged with systemd: the user timer knos-upgrade.timer runs the upgrade at {stamp}" in done.stdout
    assert "cancel it: bash scripts/schedule_upgrade.sh --cancel     (or: systemctl --user stop knos-upgrade.timer)" in done.stdout
    assert "node scripts/governance.mjs upgrade execute <index>, then knos status" in done.stdout and str(keys / "upgrade-run.log") in done.stdout
    assert (keys / "upgrade-run.timer").read_text(encoding="utf-8").split() == ["systemd", "knos-upgrade"]
    assert oct((keys / "upgrade-run.env").stat().st_mode & 0o777) == "0o600" and "export PATH=" in (keys / "upgrade-run.env").read_text(encoding="utf-8")
    # nothing was sent: arranging is not running
    assert not [c for c in _calls(calls) if c.startswith(("node governance", "knos"))]
    # again: the earlier timer is taken back first, so there is never more than one
    again = run(SYSTEMD="1")
    assert again.returncode == 0 and "replacing the earlier arrangement: cancelled: the systemd timer knos-upgrade is gone" in again.stdout
    assert "systemctl --user stop knos-upgrade.timer" in _calls(calls) and len([c for c in _calls(calls) if c.startswith("systemd-run")]) == 2
    gone = run("--cancel")
    assert gone.returncode == 0 and "cancelled: the systemd timer knos-upgrade is gone" in gone.stdout and not (keys / "upgrade-run.timer").exists()
    assert run("--cancel").stdout.startswith("nothing is arranged")


def test_without_systemd_at_is_used_when_its_daemon_runs_and_the_job_number_is_kept_for_the_cancel(box):
    keys, calls, plan, run, _ = box
    done = run(ATD="1")
    assert done.returncode == 0, done.stderr
    [made] = [c for c in _calls(calls) if c.startswith("at ")]
    assert made.startswith("at -t " + time.strftime("%Y%m%d%H%M.%S", time.gmtime(plan["run_at"]))) and made.endswith(f"/bin/bash {SCRIPT} --run")
    assert f"KNOS_KEYS={keys}" in made
    assert "arranged with at: job 17 runs the upgrade at" in done.stdout and "(or: atrm 17)" in done.stdout
    assert run("--cancel").returncode == 0 and "atrm 17" in _calls(calls)


def test_with_neither_a_windows_task_starts_wsl_at_a_utc_instant_and_with_none_it_says_what_to_do(box):
    keys, calls, plan, run, bin_dir = box
    done = run(WSL_DISTRO_NAME="Ubuntu-24.04")
    assert done.returncode == 0, done.stderr
    [made] = [c for c in _calls(calls) if c.startswith("schtasks.exe")]
    assert made == "schtasks.exe /Create /TN KnosUpgrade /XML C:\\keys\\upgrade-run.task.xml /F"
    xml = (keys / "upgrade-run.task.xml").read_bytes()
    assert xml[:2] == b"\xff\xfe"
    task = xml[2:].decode("utf-16-le")
    assert f"<StartBoundary>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(plan['run_at']))}</StartBoundary>" in task
    assert f"<Command>wsl.exe</Command><Arguments>-d Ubuntu-24.04 -- env KNOS_KEYS={keys} /bin/bash {SCRIPT} --run</Arguments>" in task
    assert "arranged with the Windows Task Scheduler: the task KnosUpgrade starts wsl.exe -d Ubuntu-24.04" in done.stdout
    assert "(or: schtasks.exe /Delete /TN KnosUpgrade /F)" in done.stdout and "NOTE: this is WSL" not in done.stdout
    assert run("--cancel").returncode == 0 and "schtasks.exe /Delete /TN KnosUpgrade /F" in _calls(calls)
    # under WSL a systemd or at timer needs the distribution running: said, with the way out
    warned = run(SYSTEMD="1", WSL_DISTRO_NAME="Ubuntu-24.04")
    assert "NOTE: this is WSL. A systemd timer fires only if this distribution is running at that time" in warned.stdout and "--with schtasks" in warned.stdout
    forced = run("--with", "schtasks", SYSTEMD="1", WSL_DISTRO_NAME="Ubuntu-24.04")
    assert forced.returncode == 0 and "arranged with the Windows Task Scheduler" in forced.stdout
    (bin_dir / "schtasks.exe").unlink()
    none = run()
    assert none.returncode == 1 and "no timer works here" in none.stderr and "bash scripts/schedule_upgrade.sh --run" in none.stderr


def test_a_time_that_has_passed_or_a_missing_schedule_arranges_nothing(box):
    keys, calls, plan, run, _ = box
    (keys / "upgrade-schedule.json").write_text(json.dumps({**plan, "run_at": int(time.time()) - 5}), encoding="utf-8")
    late = run(SYSTEMD="1")
    assert late.returncode == 1 and "has passed already. Run it now: bash scripts/schedule_upgrade.sh --run" in late.stderr
    (keys / "upgrade-schedule.json").unlink()
    none = run(SYSTEMD="1")
    assert none.returncode == 1 and "Propose the upgrades first: bash scripts/deploy_v2.sh --propose" in none.stderr
    assert not [c for c in _calls(calls) if c.startswith("systemd-run")] and not (keys / "upgrade-run.timer").exists()


def test_the_run_executes_each_proposal_in_order_then_knos_status_and_logs_every_line(box):
    keys, calls, plan, run, _ = box
    done = run("--run")
    assert done.returncode == 0, done.stderr
    assert [c for c in _calls(calls) if c.startswith(("node", "knos"))] == [
        f"node governance upgrade execute 3 --rpc https://api.devnet.solana.com keys={keys}",
        f"node governance upgrade execute 4 --rpc https://api.devnet.solana.com keys={keys}",
        "knos status rpc=https://api.devnet.solana.com"]
    log = (keys / "upgrade-run.log").read_text(encoding="utf-8")
    assert "the scheduled upgrade run starts (cluster https://api.devnet.solana.com; proposals 3 4)" in log
    assert log.index("proposal 3 of the upgrade multisig is executed") < log.index("proposal 4 of the upgrade multisig is executed") < log.index("12 of 12 checks pass")
    assert log.rstrip().endswith("done: every proposal is executed") and "done: every proposal is executed" in done.stdout
    # one that is refused is said, the other still runs, knos status still runs, and the exit code says it
    bad = run("--run", FAIL_INDEX="3")
    log = (keys / "upgrade-run.log").read_text(encoding="utf-8")
    assert bad.returncode == 1 and "refused: its time lock ends later" in log and "proposal 3: NOT executed" in log
    assert log.rstrip().endswith("done: 1 proposal(s) NOT executed. Run it again by hand: bash scripts/schedule_upgrade.sh --run")
    assert log.count("knos status") >= 2 and log.count("the scheduled upgrade run starts") == 2          # appended, never overwritten
    shown = run("--show")
    assert "schedule: proposals 3 4 on https://api.devnet.solana.com" in shown.stdout and "timer: none arranged" in shown.stdout and "NOT executed" in shown.stdout
