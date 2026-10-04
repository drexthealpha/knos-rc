"""scripts/squads_fund.mjs, offline: the instruction its dry run prints for a Squads vault is, byte for byte, the
FundOrderWallet the Python client builds with that vault as the funder (the instruction LiteSVM accepts from a wallet,
and from a PDA in tests/test_cpi_fund.py), and what it says the vault is debited is the program's arithmetic."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

from knos import mainnet_check as mc
from knos.settle.v2 import oidc, pay

ROOT = Path(__file__).resolve().parents[1]
TERMS = pay.terms_json({"accept": "", "checks": [{"app": 15368, "name": "test"}], "mode": "merge", "v": 2})
MULTISIG, MINT = Pubkey.from_string(oidc.IDS["upgrade_multisig"]), pay.USDC_DEVNET
ARGS = ["--multisig", str(MULTISIG), "--repo-id", "987654321", "--issue", "77", "--amount", "20000000", "--mint", str(MINT), "--wf-repo", "drexthealpha/Knos",
        "--wf-sha", "c" * 40, "--seq", "2", "--work-days", "7"]


def _run(*args: str, terms: str):
    node = shutil.which("node")
    if not node or not (ROOT / "scripts" / "node_modules" / "@sqds" / "multisig").is_dir():
        pytest.skip("needs Node 20 and the packages: npm ci --prefix scripts")
    return subprocess.run([node, "scripts/squads_fund.mjs", *args, "--terms", terms], cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_the_dry_run_prints_the_python_clients_instruction_for_the_vault(tmp_path):
    (tmp_path / "terms.json").write_text(TERMS.decode() + "\n", encoding="utf-8")
    done = _run(*ARGS, terms=str(tmp_path / "terms.json"))
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    vault = mc.vault_address(MULTISIG, Pubkey.from_string(oidc.IDS["squads_program"]))
    assert out["dry_run"] is True and out["vault"] == str(vault) == oidc.IDS["upgrade_authority"]
    want = pay.fund_order_wallet_ix(vault, pay.ata(vault, MINT), MINT, 987654321, 77, 20_000_000, "drexthealpha/Knos", "c" * 40, TERMS, pay.MERGE, 7 * 86_400, 2)
    assert out["instruction"] == {"program": str(pay.PAY_ID), "data": bytes(want.data).hex(),
                                  "accounts": [{"pubkey": str(a.pubkey), "signer": a.is_signer, "writable": a.is_writable} for a in want.accounts]}
    assert (out["order"], out["order_token"], out["vault_token"]) == (str(want.accounts[1].pubkey), str(want.accounts[2].pubkey), str(pay.ata(vault, MINT)))
    assert (out["amount"], out["fee"], out["debit"]) == ("20000000", str(pay.order_fee(20_000_000)), str(20_000_000 + pay.order_fee(20_000_000)))
    assert out["terms_sha256"] == pay.terms_hash(TERMS).hex() and out["refundable_after_seconds"] == 7 * 86_400
    # the terms given inline are the same order
    assert json.loads(_run(*ARGS, terms=TERMS.decode()).stdout)["instruction"] == out["instruction"]


def test_what_cannot_be_an_order_is_refused_in_words():
    for change, said in (({"--wf-sha": "main"}, "--wf-sha is a commit"), ({"--work-days": "91"}, "--work-days is 1 to 90"), ({"--mint": "usdc"}, "--mint is not a Solana address"),
                         ({"--amount": "20.5"}, "--amount is a whole number"), ({"--wf-repo": "Knos"}, "--wf-repo is owner/name")):
        args = [a if i % 2 == 0 else change.get(ARGS[i - 1], a) for i, a in enumerate(ARGS)]
        bad = _run(*args, terms=TERMS.decode())
        assert bad.returncode == 1 and bad.stderr.startswith("refused: ") and said in bad.stderr, (change, bad.stderr)
    bad = _run(*ARGS, terms="not json")
    assert bad.returncode == 1 and "--terms is the terms JSON" in bad.stderr
    bad = _run(*ARGS, "stray", terms=TERMS.decode())
    assert bad.returncode == 1 and bad.stderr.startswith("refused: ") and "--help" in bad.stderr
    bad = _run(*ARGS, "--send", terms=TERMS.decode())
    assert bad.returncode == 1 and "--member FILE is needed" in bad.stderr            # nothing is sent without a member's key
