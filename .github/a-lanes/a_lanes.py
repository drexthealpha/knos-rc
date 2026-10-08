"""Staging only (branch a0322-lanes, never in the release): order lanes on GitHub runners.

    python .github/a-lanes/a_lanes.py run --out part.json      # on each runner, KNOS_RELAY_PARTITION=i/n
    python .github/a-lanes/a_lanes.py judge parts/*.json       # after them: what each runner took

Each runner asks GitHub for real OIDC tokens of THIS repository (one owner): for each of 40 orders a `take` and a
`pay` token (audience knos3:<word>:<order>:<n>), and 8 fundings (knos3:fund:..., the owner's lane). Each token's lane
is the relay's own (`knos.proof.ghrelay._lane`), it is queued in the relay's own queue (`relayq.Queue`, its part from
KNOS_RELAY_PARTITION) and carried by the relay's own workers (`relayq.work(..., partition=True)`). The chain is a
stand-in that only notes what it was handed (no such order exists; nothing is signed or sent). No token is printed or
written: the output holds keys, lanes, orders, the owner id and counts.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

ORDERS, FUNDINGS = 40, 8


def orders() -> list[str]:
    from solders.pubkey import Pubkey
    return [str(Pubkey.from_bytes(hashlib.sha256(f"a0322-lanes-{i}".encode()).digest())) for i in range(ORDERS)]


def mint(aud: str) -> str:
    url = os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"] + "&audience=" + urllib.parse.quote(aud, safe="")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["value"]


def claims(jwt: str) -> dict:
    body = jwt.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def run(out: str) -> int:
    from knos.proof import ghrelay
    from knos.settle.v2 import relayq
    part = relayq.partition()
    named = orders()
    wanted = []                         # (key, kind, audience, order or None), in the order GitHub issues them
    for i, o in enumerate(named):
        wanted.append((f"take-{i}", "take", f"knos3:take:{o}:1", o))
        wanted.append((f"pay-{i}", "pay", f"knos3:pay:{o}:2", o))
    for j in range(FUNDINGS):
        wanted.append((f"fund-{j}", "fund", f"knos3:fund:1:5000000:0:{'b' * 64}:1209600:{named[j]}:{j}", None))
    q = relayq.Queue(Path(tempfile.mkdtemp()) / "relay.json", part=part)
    owners, lanes = set(), {}
    for key, kind, aud, o in wanted:
        jwt = mint(aud)
        c = claims(jwt)
        assert c.get("iss") == "https://token.actions.githubusercontent.com" and c.get("aud") == aud, "not a GitHub token for this audience"
        owners.add(str(c.get("repository_owner_id")))
        lane = ghrelay._lane(kind, jwt, os.environ.get("GITHUB_REPOSITORY", ""))
        lanes[key] = lane
        q.put(key, lane, {"kind": kind, "order": o, "jwt": jwt}, kind=kind)
    took, lock = [], threading.Lock()

    def handle(entry: dict) -> dict:           # the stand-in chain: note what was handed, and when
        time.sleep(0.05)
        with lock:
            took.append({"key": entry["key"], "lane": entry["lane"], "kind": entry["item"]["kind"], "order": entry["item"]["order"],
                         "worker": entry.get("worker"), "at": time.time()})
        return {"ok": True}

    counts = relayq.work(q, handle, workers=4, partition=True, owner=f"runner{part[0] if part else 'all'}")
    result = {"part": list(part) if part else None, "runner": os.environ.get("RUNNER_NAME"), "run": os.environ.get("GITHUB_RUN_ID"),
              "owners": sorted(owners), "queued": len(wanted), "took": took, "counts": counts, "lanes": lanes}
    Path(out).write_text(json.dumps(result, indent=1))
    by_kind: dict[str, int] = {}
    for t in took:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + 1
    print(f"runner {result['runner']} part {part}: GitHub signed {len(wanted)} tokens, owner id(s) {sorted(owners)}; "
          f"this runner carried {len(took)} {by_kind} in {len({t['lane'] for t in took})} lanes; queue counts {counts}")
    return 0


def judge(files: list[str]) -> int:
    parts = [json.loads(Path(f).read_text()) for f in files]
    bad = []
    owners = set().union(*(set(p["owners"]) for p in parts))
    n = len(parts)
    where: dict[str, set] = {}
    at: dict[str, float] = {}
    for p in parts:
        for t in p["took"]:
            where.setdefault(t["key"], set()).add(p["part"][0])
            at[t["key"]] = t["at"]
    pay_keys = [f"{w}-{i}" for i in range(ORDERS) for w in ("take", "pay")]
    fund_keys = [f"fund-{j}" for j in range(FUNDINGS)]
    if len(owners) != 1:
        bad.append(f"owners {sorted(owners)}: expected one")
    missing = [k for k in pay_keys + fund_keys if k not in where]
    twice = [k for k, s in where.items() if len(s) > 1]
    if missing:
        bad.append(f"carried by no runner: {missing}")
    if twice:
        bad.append(f"carried by two runners: {twice}")
    per = {p["part"][0]: sorted({t["order"] for t in p["took"] if t["kind"] in ("take", "pay")}) for p in parts}
    for p in sorted(parts, key=lambda p: p["part"][0]):
        i = p["part"][0]
        print(f"part {i}/{n} (runner {p['runner']}): {len(per[i])} orders ({2 * len(per[i])} order tokens), "
              f"{sum(1 for t in p['took'] if t['kind'] == 'fund')} fundings, {len(p['took'])} tokens carried")
    busy = sum(1 for v in per.values() if v)
    if busy < 2:
        bad.append(f"the orders of one owner went to {busy} runner(s)")
    for i in range(ORDERS):          # one order: both its tokens on one runner, take before pay
        a, b = where.get(f"take-{i}", set()), where.get(f"pay-{i}", set())
        if a != b:
            bad.append(f"order {i}: take on {sorted(a)}, pay on {sorted(b)}")
        elif at.get(f"take-{i}", 0) > at.get(f"pay-{i}", 0):
            bad.append(f"order {i}: pay left before take")
    fund_parts = set().union(*(where.get(k, set()) for k in fund_keys))
    if len(fund_parts) != 1:
        bad.append(f"fundings of one owner on parts {sorted(fund_parts)}: expected one (the owner's lane)")
    print(f"one owner (GitHub repository_owner_id {sorted(owners)}): {ORDERS} orders x 2 tokens spread over {busy} of {n} runners, "
          f"each order on ONE runner, take before pay in every order; {FUNDINGS} fundings in the owner's lane, on part {sorted(fund_parts)}")
    for b in bad:
        print("FAIL:", b)
    print("ORDER LANES OK" if not bad else "ORDER LANES FAILED")
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1] == "run":
        sys.exit(run(sys.argv[3] if len(sys.argv) > 3 else "part.json"))
    sys.exit(judge(sys.argv[2:]))
