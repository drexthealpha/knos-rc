#!/bin/bash
# staging harness (A 0.3.21): base/ (a git repository: the default branch) and pr/ (git archive of the head), changed.txt
set -euo pipefail
T=$1; SRC=$(cd "$(dirname "$0")/.." && pwd)
mk() {  # name, base folder, submission folder
  local d=$T/$1; mkdir -p "$d"; cp -r "$2" "$d/base"
  git -C "$d/base" init -q -b main; git -C "$d/base" add -A; git -C "$d/base" -c user.name=ci -c user.email=ci@example.invalid commit -qm base
  git -C "$d/base" checkout -q -b pr; cp -r "$3"/. "$d/base/"; git -C "$d/base" add -A
  git -C "$d/base" -c user.name=ci -c user.email=ci@example.invalid commit -qm pr
  head=$(git -C "$d/base" rev-parse HEAD); git -C "$d/base" checkout -q main
  mkdir "$d/pr"; git -C "$d/base" archive "$head" | tar -x -C "$d/pr"
  git -C "$d/base" diff --no-renames --name-only "main...$head" > "$d/changed.txt"
  echo "$1: changed $(tr '\n' ' ' < "$d/changed.txt")"
}
mk classify "$SRC/examples/acceptance/classify/base" "$SRC/examples/acceptance/classify/solution"
# a pytest suite that works in parallel: threads, and one process per core, as a parallel test suite does
P=$(mktemp -d); mkdir -p "$P/base/.knos/acceptance/1" "$P/fix"
printf 'def total(xs):\n    return 0\n' > "$P/base/calc.py"
printf 'def total(xs):\n    return sum(xs)\n' > "$P/fix/calc.py"
cat > "$P/base/.knos/acceptance/1/test_total.py" <<'PY'
import os
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

from calc import total


def _chunk(n):
    time.sleep(0.5)                     # each worker holds its task long enough to be counted
    return total(range(n))


def test_total_in_parallel_threads():
    with ThreadPoolExecutor(max_workers=32) as pool:
        assert list(pool.map(_chunk, range(64))) == [n * (n - 1) // 2 for n in range(64)]


def test_total_in_one_process_per_core():
    with ProcessPoolExecutor(max_workers=os.cpu_count() or 2) as pool:
        assert list(pool.map(_chunk, range(16))) == [n * (n - 1) // 2 for n in range(16)]
PY
mk parallel "$P/base" "$P/fix"
