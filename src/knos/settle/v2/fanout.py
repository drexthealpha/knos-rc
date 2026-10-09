"""One signed transaction sent to more than one endpoint, sent again on a schedule, and confirmed by its signature.

The 0.3.24 burst on devnet paid 22 of 40: the other 18 were turned away by the public endpoint (dropped connections,
timeouts, HTTP 408), each a payment whose ONE send's answer never came. A transaction's signature is its identity: the
same signed bytes land at most once, however often and wherever they are sent. So `FanLedger` (a `knos.chain.Ledger`)

  * sends each signed transaction to the configured endpoint and to every second endpoint it is given, the first with
    preflight (a failing program still answers with its logs, and costs no fee), the others without;
  * takes a dropped connection, a timeout, HTTP 408, 429 or 5xx as no answer, never as a refusal;
  * asks for the signature's status (getSignatureStatuses, at any endpoint that answers) every POLL_S seconds and sends
    the same bytes again every RESEND_EVERY polls until it is confirmed or POLLS have passed (60 s: a blockhash is taken
    for about 60 to 90 seconds, https://solana.com/docs/core/transactions/confirmation#how-does-transaction-expiration-work);
  * raises TimeoutError("... not confirmed ...") only then, so the caller may sign again over a fresh blockhash.

Solana publishes ONE public devnet endpoint, https://api.devnet.solana.com, rate limited to 40 requests per 10 seconds
per IP for one RPC method (https://solana.com/docs/references/clusters). No second public one is named there, so by
default there is none and the fan-out is the staggered resend to the one endpoint; KNOS_RPC_SECOND (comma-separated
URLs) names more, e.g. a provider's devnet endpoint of the operator's own. Counted by poll, never by the wall clock, so
a test's stand-in clock and endpoints give the same answer every run."""
from __future__ import annotations

import base64
import os
import time
import urllib.error
from dataclasses import dataclass, field
from typing import Any, Callable

from solders.hash import Hash
from solders.keypair import Keypair

from ... import chain

POLL_S = 0.5            # between two questions about the signature's status
RESEND_EVERY = 4        # polls: the same bytes are sent again every 2 s
POLLS = 120             # 60 s in all, then TimeoutError
NO_ANSWER = (TimeoutError, ConnectionError, urllib.error.URLError, OSError)
NO_ANSWER_HTTP = {408, 425, 429, 500, 502, 503, 504}
LANDED_ALREADY = ("already been processed", "alreadyprocessed")


def seconds_from_env() -> tuple[str, ...]:
    """The second endpoints KNOS_RPC_SECOND names (comma-separated https URLs), in order, none twice."""
    raw = os.environ.get("KNOS_RPC_SECOND", "")
    return tuple(dict.fromkeys(u.strip() for u in raw.split(",") if u.strip().startswith("https://")))


def no_answer(why: BaseException) -> bool:
    """An error that says nothing of the transaction: the request or its answer was lost on the way."""
    if isinstance(why, urllib.error.HTTPError):
        return why.code in NO_ANSWER_HTTP
    if isinstance(why, chain.RpcError):
        text = str(why).lower()
        return "blockhash not found" in text or "node is behind" in text or "rate limit" in text
    return isinstance(why, NO_ANSWER)


@dataclass
class FanLedger(chain.Ledger):
    """A `chain.Ledger` whose sends reach every endpoint of `seconds` too, are sent again until confirmed, and are
    confirmed by signature status. `sleep`: how a poll waits (a test's stand-in)."""
    seconds: tuple[str, ...] = field(default_factory=seconds_from_env)
    sleep: Callable[[float], None] = time.sleep
    polls: int = POLLS
    resend_every: int = RESEND_EVERY
    asked: dict = field(default_factory=lambda: {"sends": 0, "resends": 0, "no_answer": 0, "status_polls": 0})

    def endpoints(self) -> list[str]:
        return list(dict.fromkeys([self.url, *self.seconds]))

    def _post(self, url: str, method: str, params: list) -> Any:
        return chain.call(url, method, params)

    def _send_raw(self, raw: str, first: bool) -> None:
        """The same bytes to every endpoint; only a program's refusal at the first endpoint's preflight is raised."""
        for n, url in enumerate(self.endpoints()):
            preflight = first and n == 0
            opts = {"encoding": "base64", "preflightCommitment": "confirmed"} if preflight else {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}
            try:
                self._post(url, "sendTransaction", [raw, opts])
            except Exception as why:  # noqa: BLE001 - sorted below: no answer, landed already, or the program's own refusal
                text = str(why).lower()
                if any(m in text or m in text.replace(" ", "") for m in LANDED_ALREADY):
                    continue                # these bytes landed: an earlier send, or another endpoint's
                if no_answer(why):
                    self.asked["no_answer"] += 1
                    continue
                if preflight:
                    raise
            self.asked["sends" if first else "resends"] += 1

    def _statuses(self, sigs: list[str]) -> list[dict | None] | None:
        """Each signature's status from the first endpoint that answers; None when none did."""
        for url in self.endpoints():
            try:
                self.asked["status_polls"] += 1
                return list(self._post(url, "getSignatureStatuses", [sigs])["value"])
            except Exception as why:  # noqa: BLE001
                if not no_answer(why):
                    raise
                self.asked["no_answer"] += 1
        return None

    def _confirm(self, raws: dict[str, str]) -> None:
        """Polls until every signature of `raws` is at this ledger's commitment, sending the unconfirmed ones again
        every `resend_every` polls. RpcError for one that failed on chain; TimeoutError for one never confirmed."""
        good = ("finalized",) if self.commitment == "finalized" else ("confirmed", "finalized")
        done: dict[str, dict] = {}
        for n in range(1, self.polls + 1):
            waiting = [s for s in raws if s not in done]
            got = self._statuses(waiting)
            for sig, status in zip(waiting, got or []):
                if status and status.get("confirmationStatus") in good:
                    done[sig] = status
            failed = next((done[s] for s in raws if s in done and done[s].get("err")), None)
            if failed is not None:
                raise chain.RpcError(f"transaction failed: {failed['err']}", failed)
            if len(done) == len(raws):
                return
            if n % self.resend_every == 0:
                for sig in raws:
                    if sig not in done:
                        self._send_raw(raws[sig], first=False)
            self.sleep(POLL_S)
        lost = next(s for s in raws if s not in done)
        raise TimeoutError(f"{lost} not confirmed after {self.polls} polls ({self.polls * POLL_S:.0f} s), sent to {len(self.endpoints())} endpoint(s)")

    def _blockhash(self):
        last: BaseException | None = None
        for url in self.endpoints():
            try:
                return Hash.from_string(self._post(url, "getLatestBlockhash", [{"commitment": "confirmed"}])["value"]["blockhash"])
            except Exception as why:  # noqa: BLE001
                if not no_answer(why):
                    raise
                last = why
        raise TimeoutError(f"no endpoint gave a blockhash: {last}")

    def _submit(self, tx) -> str:
        raw = base64.b64encode(bytes(tx)).decode()
        self._send_raw(raw, first=True)
        return str(tx.signatures[0])

    def send(self, ixs, payer: Keypair, signers: list[Keypair] | None = None, v1: bool = False) -> str:
        tx = chain.sign(ixs, payer, signers, self._blockhash(), v1)
        sig = self._submit(tx)
        self._confirm({sig: base64.b64encode(bytes(tx)).decode()})
        return sig

    def send_all(self, groups, payer: Keypair, signers: list[Keypair] | None = None, v1: bool = False) -> list[str]:
        groups = [list(g) for g in groups]
        if len(groups) < 2:
            return [self.send(g, payer, signers, v1) for g in groups]
        blockhash = self._blockhash()
        txs = [chain.sign(ixs, payer, signers, blockhash, v1) for ixs in groups]
        raws = {str(tx.signatures[0]): base64.b64encode(bytes(tx)).decode() for tx in txs}
        refused: BaseException | None = None
        for tx in txs:
            try:
                self._submit(tx)
            except Exception as why:  # noqa: BLE001 - raised once the others are confirmed, as chain.Ledger.send_all does
                refused = refused or why
                raws.pop(str(tx.signatures[0]), None)
        if raws:
            self._confirm(raws)
        if refused is not None:
            raise refused
        return [str(tx.signatures[0]) for tx in txs]
