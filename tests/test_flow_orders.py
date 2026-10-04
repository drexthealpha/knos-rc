"""knos.flow with knos_pay 2.1 behind it: a funding comment opens a WORK ORDER, settle pays orders and jobs alike, and the
order's own commands (offer, raise, cancel, split, take). The fakes are tests/_flow.py's; `w.version = 1` is a chain
whose escrow is 2.1. While it is 0 every comment does what it did before (tests/test_flow.py)."""
from __future__ import annotations

from _flow import ADDRESS, EVE, HUBOT, MONA, REPO_ID, WALLET, World, check, key
from _hub import user
from knos import commands, flow, policy, terms
from knos.settle.v2 import pay
from test_flow import BOUGHT, EXPLORER, FAUCET, MONEY, plain

ERIN = user("erin", 77)
ORDER = pay.order_pda(pay.scope_of(REPO_ID, 7), FAUCET, 0)
LINK = f"[order on Solana]({EXPLORER}/address/{ORDER}?cluster=devnet)"
NEUTRAL = ("After a merge the seller can have it paid without this repository's workflow: `knos settle --neutral <the pull request's "
           "URL>` asks GitHub to sign in a repository of their own.")


def world(tmp_path, version: int = 1, **kw) -> World:
    w = World(tmp_path, **kw)
    w.version = version
    w.hub.issue(7, "Slugify keeps punctuation.")
    w.hub.users["erin"] = ERIN
    w.hub.required = [{"context": "test", "integration_id": 15368}]
    return w


def ordered(tmp_path, bought: dict = BOUGHT, units: int = 20_000_000, **more) -> World:
    """Issue #7 with a work order funded an hour ago, and pull request #12 open for it with its checks passed."""
    w = world(tmp_path)
    w.chain.order(7, units, bought, **more)
    w.clock.sleep(3600)
    w.hub.checks[w.hub.pull(12, MONA, "Fixes #7")["head"]["sha"]] = [check("test"), check("build")]
    return w


def said(w: World, n: int, who_: dict, body: str, code: int = 0, most: int = 1500) -> str:
    assert flow.command(w.run(w.hub.commented(n, who_, body))) == code
    return plain(w.hub.knos(n)[-1], most)


def settled(w: World, n: int = 12, code: int = 0) -> str:
    assert flow.settle(w.run(w.hub.merge(n))) == code
    return plain(w.hub.knos(n)[-1], 1500)


def test_a_funding_comment_opens_a_work_order_and_the_reply_says_the_fee_the_warranty_the_arbiter_and_who_can_settle(tmp_path):
    w = world(tmp_path)
    got = said(w, 7, HUBOT, "/knos fund 20 checks: test warranty 14 holdback 20 arbiter @erin neutral off")
    raw = w.chain.logs[str(ORDER)]
    options = pay.opts(0, 2000, 14, 0, 7, 0, ERIN["id"])
    assert w.signer.asked == [pay.order_fund_audience(7, 20_000_000, pay.MERGE, pay.terms_hash(raw), FAUCET, 14 * 86_400, 0, options)]
    (address, o), = w.chain.orders(7)
    assert address == str(ORDER) and (o.amount, o.fee, o.holdback_bps, o.warranty_s, o.arbiter_id, o.reserve_days) == (20_000_000, 500_000, 2000, 14 * 86_400, 77, 7)
    assert not o.flags & pay.F_NEUTRAL and w.chain.jobs() == []
    assert got.startswith(f"Knos: 20.00 {MONEY} from the devnet faucet is in escrow for issue #7 as a work order ({LINK}), 30 s after the comment. "
                          "The funder pays Knos's fee of 0.50 on top, so whoever is paid receives the full amount.\n\n")
    assert ("Warranty: 20% of each payment is held back for 14 days after it is paid; if the work is reverted in that time, that part goes "
            "back to the funder. Arbiter: @erin rules if a payment is disputed. If it is not paid by 2026-10-05 14:13 UTC, the money and the "
            "fee go back to where they came from.") in got
    assert got.endswith("Only this repository's workflow can have it paid (`neutral off`).") and "`test` (the checks you named)" in got
    # unsaid: no warranty, no arbiter, and neutral, which the reply spells out; a second order from the same Balance takes the next number
    got = said(w, 7, HUBOT, "/knos fund 5")
    second = pay.order_pda(pay.scope_of(REPO_ID, 7), FAUCET, 1)
    assert w.signer.asked[-1].split(":")[8:] == ["1", pay.opts(pay.F_NEUTRAL, reserve_days=7).hex()] and str(second) in got
    assert "The funder pays Knos's fee of 0.40 on top" in got and "No warranty: a payment is final when it is made. No arbiter is named." in got
    assert got.endswith(NEUTRAL) and len(w.chain.orders(7)) == 2
    # what cannot be an order is said with the comment that can
    assert said(w, 7, HUBOT, "/knos fund 2") == ("Knos: nothing was funded. A work order holds from 5 to 500, and this one asks for 2. Comment "
                                               "`/knos fund 5` instead.")
    assert "`holdback` keeps a share of each payment back until the warranty ends, so it needs one: add `warranty 14`" in said(w, 7, HUBOT, "/knos fund 20 holdback 10")
    assert "GitHub gave no account named @nobody-here (the arbiter)" in said(w, 7, HUBOT, "/knos fund 20 arbiter @nobody-here")
    assert len(w.chain.orders(7)) == 2 and len(w.signer.asked) == 2


def test_the_repositorys_policy_decides_who_may_fund_how_much_and_what_an_order_asks_when_its_funder_does_not_say(tmp_path):
    w = world(tmp_path)
    w.hub.can["mona"] = "write"
    w.hub.contents[".knos/policy.yml"] = ("version: 1\nwho_may_fund: [hubot]\ncap_per_order: 50\nmonthly_budget: 100\nchecks: [build]\n"
                                          "warranty_days: 30\nholdback_percent: 10\narbiter: erin\n")
    rules = policy.load(w.hub.contents[".knos/policy.yml"])
    assert said(w, 7, MONA, "/knos fund 20") == ("Knos: nothing was funded. .knos/policy.yml line 2: only hubot may fund; mona (4242) is not one of "
                                              "them. Someone who can write to this repository changes that file on its default branch.")
    assert ".knos/policy.yml line 3: an order may take at most 50, and this one is 60." in said(w, 7, HUBOT, "/knos fund 60")
    w.month_spent = 90
    assert ".knos/policy.yml line 4: the monthly budget is 100 and 90 is already spent this month, so 20 more would pass it (10 left)." in said(w, 7, HUBOT, "/knos fund 20")
    assert w.signer.asked == [] and w.chain.orders() == []
    w.month_spent = 0
    got = said(w, 7, HUBOT, "/knos fund 20", most=1800)
    (_a, o), = w.chain.orders(7)
    assert (o.holdback_bps, o.warranty_s, o.arbiter_id) == (1000, 30 * 86_400, ERIN["id"]) and o.flags & pay.F_NEUTRAL
    bought = terms.parse(w.chain.logs[str(ORDER)])
    assert bought["policy"] == policy.digest(rules) and bought["checks"] == [{"app": -1, "name": "build"}]     # hashed into the order's terms
    assert "`build` (any source) (the checks `.knos/policy.yml` names)" in got and "Warranty: 10% of each payment is held back for 30 days" in got
    assert "Arbiter: @erin rules if a payment is disputed." in got
    # the comment's own words come before the policy's defaults
    assert "No warranty" not in said(w, 7, HUBOT, "/knos fund 20 warranty 7 holdback 0 checks: test") and w.chain.orders(7)[-1][1].holdback_bps in (0, 1000)
    # a policy that does not load stops funding: it never means "anything goes"
    w.hub.contents[".knos/policy.yml"] = "version: 1\ncap_per_ordr: 50\n"
    got = said(w, 7, HUBOT, "/knos fund 20")
    assert got.startswith("Knos: nothing was funded. This repository's policy cannot be used (.knos/policy.yml line 2: `cap_per_ordr` is not a rule; did you mean `cap_per_order`?), and funding stops until it can be.")
    assert len(w.chain.orders(7)) == 2


def test_a_merge_pays_a_work_order_in_full_and_the_token_names_the_order_the_pull_request_and_the_payee(tmp_path):
    w = ordered(tmp_path)
    w.chain.bind(MONA)
    got = settled(w)
    head = w.hub.pulls[12]["head"]["sha"]
    assert w.signer.asked == [pay.order_pay_audience(ORDER, head, pay.terms_hash(terms.canonical(BOUGHT)), pay.MERGE, 12, [(MONA["id"], 10_000, None)])]
    assert got == (f"Knos: paid. @mona received 20.00 {MONEY} for issue #7, in full: its funder paid Knos's fee of 0.50 on top. It went to "
                   f"`{WALLET}`, the wallet bound to @mona's GitHub account ([transaction]({EXPLORER}/tx/sig2?cluster=devnet), 30 s after the merge).")
    assert w.chain.orders() == [] and w.screened == [WALLET]
    # no wallet: held for the payee, in full
    w = ordered(tmp_path / "held")
    got = settled(w)
    assert got.startswith(f"Knos: held for @mona. 20.00 {MONEY} for issue #7 waits for them until ") and "It is then paid in full" in got
    assert w.chain.orders(7)[0][1].state == "held" and w.signer.asked[-1].endswith(f":12:{MONA['id']}.10000.-")
    # a job and an order on one issue are both paid by one merge: each by its own token
    w = ordered(tmp_path / "both")
    w.chain.fund(7, 10_000_000, BOUGHT, at=w.clock() - 3600)
    w.chain.bind(MONA)
    got = settled(w)
    assert sorted(a.split(":")[0] for a in w.signer.asked) == ["knos2", "knos3"] and got.lower().count("paid. @mona received") == 2
    # a holdback stays in the order as its warranty, and a failed check pays nothing
    w = ordered(tmp_path / "warranty", holdback_bps=2000, warranty_days=14)
    w.chain.bind(MONA)
    got = settled(w)
    assert "@mona received 16.00" in got and "4.00 more (20%) is held back as the warranty for 14 days" in got
    w = ordered(tmp_path / "failed")
    w.hub.checks[w.hub.pulls[12]["head"]["sha"]] = [check("test", "failure"), check("build")]
    assert settled(w).startswith(f"Knos: not paid. This pull request does not take the work order on issue #7 (20.00 {MONEY}) as it stands")
    assert w.signer.asked == []


def test_a_maintainers_split_before_the_merge_pays_up_to_four_people_and_only_when_each_has_a_wallet(tmp_path):
    w = ordered(tmp_path)
    w.chain.bind(MONA)
    w.chain.bind(EVE, ADDRESS)
    assert said(w, 12, EVE, "/knos split @mona 60 @eve 40").startswith("Knos: `/knos split` is for people with write access to this repository.")
    got = said(w, 12, HUBOT, "/knos split @mona 60 @eve 40")
    assert got.startswith("Knos: noted. If this pull request is merged and takes a work order, the order pays @mona 60%, @eve 40%.")
    got = settled(w)
    assert w.signer.asked[-1].endswith(f":12:{MONA['id']}.6000.-,{EVE['id']}.4000.-")
    assert got.startswith(f"Knos: paid. The work order on issue #7 paid 20.00 {MONEY} in full (its funder paid Knos's fee of 0.50 on top): "
                          f"@mona 12.00 (60%) to `{WALLET}`, @eve 8.00 (40%) to `{ADDRESS}` (")
    assert sorted(w.screened) == sorted([WALLET, ADDRESS])
    # a split after the merge counts for nothing, and one that names someone with no wallet is not paid at all
    assert said(w, 12, HUBOT, "/knos split @eve 100") == "Knos: this pull request is already merged, and `/knos split` counts only before the merge. Nothing was noted."
    w = ordered(tmp_path / "nowallet")
    w.chain.bind(MONA)
    said(w, 12, HUBOT, "/knos split @mona 60 @eve 40")
    got = settled(w)
    assert "`/knos split` names @eve, and no wallet is known for them: a split is paid only when every person has one" in got and w.signer.asked == []
    # while the escrow is 2.0 there is nothing to split
    w = ordered(tmp_path / "v0")
    w.version = 0
    assert "A split is for a work order" in said(w, 12, HUBOT, "/knos split @mona 60 @eve 40")


def test_a_policy_that_names_payees_is_enforced_and_every_address_is_screened_before_a_payout(tmp_path):
    w = ordered(tmp_path)
    w.chain.bind(MONA)
    w.hub.contents[".knos/policy.yml"] = "version: 1\npayees: [eve]\n"
    got = settled(w)
    assert "- .knos/policy.yml line 2: only eve may be paid; mona is not one of them" in got and w.signer.asked == [] and len(w.chain.orders()) == 1
    # jobs alike
    w = World(tmp_path / "job")
    w.hub.issue(7)
    w.chain.fund(7, 20_000_000, BOUGHT)
    w.clock.sleep(3600)
    w.hub.checks[w.hub.pull(12, MONA, "Fixes #7")["head"]["sha"]] = [check("test"), check("build")]
    w.chain.bind(MONA)
    w.hub.contents[".knos/policy.yml"] = "version: 1\npayees: [eve]\n"
    assert "only eve may be paid; mona is not one of them" in settled(w) and w.signer.asked == []
    # listed: nothing is signed, the money stays in escrow, and the comment says so
    for version in (0, 1):
        w = ordered(tmp_path / f"listed{version}")
        if not version:
            w.version = 0
            w.chain.accounts.clear()
            w.chain.fund(7, 20_000_000, BOUGHT, at=w.clock() - 3600)
        w.chain.bind(MONA)
        w.screen = lambda address: (False, f"{address} is on the US Treasury's sanctions list (OFAC SDN, digital currency addresses as of 2026-09-20): the payout is refused and held, not sent.")
        got = settled(w)
        assert got.startswith("Knos: held, not paid. The ") and f"met its terms, but {WALLET} is on the US Treasury's sanctions list" in got
        assert "the payout is refused and held, not sent. The money stays in escrow until 2026-10-05 14:13 UTC" in got and w.signer.asked == []
        # not screened: it pays, and says so
        w.screen = lambda address: (None, "not screened: the Treasury's list could not be had (502).")
        assert flow.command(w.run(w.hub.commented(12, EVE, "/knos settle"))) == 0
        assert flow.settle(w.run(w.hub.commented(12, EVE, "/knos settle"))) == 0
        got = plain(w.hub.knos(12)[-1], 1500)
        assert got.startswith("Knos: paid. @mona received ") and got.endswith(f"`{WALLET}` was not screened: the Treasury's list could not be had (502).")


def test_a_standing_offer_pays_its_vendor_for_each_accepted_change_until_its_budget_is_spent(tmp_path):
    w = world(tmp_path)
    w.chain.bind(MONA)
    got = said(w, 7, HUBOT, "/knos offer @mona rate 12 budget 30 checks: test")
    (address, o), = w.chain.orders(7)
    assert o.flags & pay.F_STANDING and (o.rate, o.amount) == (12_000_000, 30_000_000) and terms.parse(w.chain.logs[address])["vendor"] == MONA["id"]
    assert w.signer.asked[-1].split(":")[9] == pay.opts(pay.F_NEUTRAL | pay.F_STANDING, rate=12_000_000).hex()
    assert got.startswith(f"Knos: a standing offer for @mona is in escrow on issue #7: 30.00 {MONEY} from the devnet faucet (")
    assert "Each pull request of @mona's that says `Fixes #7` and is accepted is paid 12.00 in full, until the 30.00 is spent." in got and NEUTRAL in got
    w.clock.sleep(3600)
    w.hub.checks[w.hub.pull(12, MONA, "Fixes #7")["head"]["sha"]] = [check("test")]
    got = settled(w)
    assert "@mona received 12.00" in got and "The offer stays open: 18.00 of 30.00 is left for further accepted changes until " in got
    assert w.chain.orders(7)[0][1].paid == 12_000_000
    # anyone else's pull request is not the vendor's
    w.hub.issues[7]["state"] = "open"
    w.chain.bind(EVE, ADDRESS)
    w.hub.checks[w.hub.pull(13, EVE, "Fixes #7")["head"]["sha"]] = [check("test")]
    assert f"this standing offer pays one vendor (GitHub user id {MONA['id']}), and this pull request pays @eve" in settled(w, 13)
    # what the escrow cannot hold, and an escrow without orders, are said with what to type
    assert said(w, 7, HUBOT, "/knos offer @mona rate 12 budget 600 checks: test") == (
        "Knos: nothing was funded. A work order holds from 5 to 500, and this one asks for 600. Comment `/knos offer @mona rate 12 budget 500` instead.")
    w.version, w.runs = 0, w.runs
    w2 = world(tmp_path / "v0", version=0)
    assert "A standing offer is a work order, and the escrow on this cluster does not hold work orders yet" in said(w2, 7, HUBOT, "/knos offer @mona rate 12 budget 30")
    w.hub.contents[".knos/policy.yml"] = "version: 1\nvendors: [acme]\n"
    w3 = world(tmp_path / "vendors")
    w3.hub.contents[".knos/policy.yml"] = "version: 1\nvendors: [acme]\n"
    assert "lists the vendors a standing offer may pay (acme), and @mona is not one of them" in said(w3, 7, HUBOT, "/knos offer @mona rate 12 budget 30")


def test_raise_says_how_cancel_gives_notice_and_take_reserves_the_order_too(tmp_path):
    w = ordered(tmp_path, reserve_days=7, kill_bps=1000)
    got = said(w, 7, EVE, "/knos raise 10")
    assert got == (f"Knos: nothing was added. The work order on issue #7 ({LINK}) holds test USDC from the devnet faucet, and the faucet tops "
                   "nothing up. `/knos fund 10` opens a second order beside this one.")
    got = said(w, 7, MONA, "/knos take")
    assert w.signer.asked == [f"knos3:take:{ORDER}:{MONA['id']}:7"] and w.chain.orders(7)[0][1].reserved_by == MONA["id"]
    assert got.startswith("Knos: issue #7 is reserved for @mona until ") and "The work order on Solana records it too: if the order is cancelled while you hold it, 10% of it is yours (" in got
    assert said(w, 7, EVE, "/knos cancel").startswith("Knos: `/knos cancel` is for people with write access to this repository.")
    got = said(w, 7, HUBOT, "/knos cancel")
    assert w.signer.asked[-1] == f"knos3:cancel:{ORDER}" and w.chain.orders(7)[0][1].cancel_at
    assert got.startswith(f"Knos: the work order on issue #7 (20.00 {MONEY}, {LINK}) is cancelled with 7 days' notice. A pull request that is merged "
                          "and meets its terms before 2026-09-28 15:14 UTC is still paid; after that the money and the fee go back to where they "
                          "came from. It is reserved, so 10% of it goes to the person who holds it first. (")
    assert "was cancelled on 2026-09-21 15:1" in said(w, 7, HUBOT, "/knos cancel")
    # real money: the reply names the balance, whose wallet signs, and the fee on top
    w = world(tmp_path / "real")
    source = w.chain.balance("treasury", 100_000_000, spenders=[HUBOT["id"]])
    w.chain.order(7, 20_000_000, BOUGHT, source=source, flags=pay.F_NEUTRAL)
    got = said(w, 7, EVE, "/knos raise 10")
    assert f"was funded from the balance `{source}`, and only the wallet that opened that balance can add to it: it signs knos_pay's TopUp for 10.00 {MONEY}, and pays Knos's fee of 0.40 on top" in got
    # a job is neither, and no order is no order
    w = world(tmp_path / "job", version=0)
    w.chain.fund(7, 20_000_000, BOUGHT)
    assert "is a job, which is neither raised nor cancelled: it runs until 2026-10-05 14:13 UTC" in said(w, 7, HUBOT, "/knos cancel")
    w = world(tmp_path / "none")
    assert said(w, 7, HUBOT, "/knos cancel") == "Knos: issue #7 has no work order, so nothing was cancelled. A maintainer opens one with `/knos fund <amount>`."
    assert all(isinstance(commands.parse(x), commands.Error) is False for x in ("/knos raise 10", "/knos cancel"))


def test_without_a_relay_key_an_orders_tokens_travel_as_comments_and_status_shows_the_order(tmp_path):
    w = world(tmp_path, relay_key=False)
    got = said(w, 7, HUBOT, "/knos fund 20")
    assert f"as a work order ({LINK})" in got and "Knos's fee of 0.50 on top" in got and [x[1] for x in w.worker.posted()] == ["fund"]
    assert f"20.00 {MONEY} is in escrow for issue #7 as a work order until 2026-10-05" in said(w, 7, EVE, "/knos status")
    w.clock.sleep(3600)
    w.hub.checks[w.hub.pull(12, MONA, "Fixes #7")["head"]["sha"]] = [check("test")]
    w.chain.bind(MONA)
    assert settled(w).startswith(f"Knos: paid. @mona received 20.00 {MONEY} for issue #7, in full")
    assert [x[1] for x in w.worker.posted()] == ["fund", "proof"]
    assert key("x") != ORDER


# ---- knos attest: the same rules, from anyone's repository ------------------------------------------------------------

def attested(w: World, kind: str, pull: int | None = 12, payees: str = "", who_: dict = MONA, order=ORDER, **env) -> tuple[int, flow.Run, str]:
    w.tmp.mkdir(parents=True, exist_ok=True)
    summary = w.tmp / f"attest-{w.runs}.md"
    run = w.run({}, **{"GITHUB_ACTOR_ID": str(who_["id"]), "GITHUB_ACTOR": who_["login"], "GITHUB_REPOSITORY_ID": "999",
                       "GITHUB_STEP_SUMMARY": str(summary), **env})
    w.signer.actor = who_["id"]
    code = flow.attest(run, str(order), kind, pull, payees)
    return code, run, summary.read_text(encoding="utf-8")


def test_attest_signs_a_payment_from_the_sellers_own_repository_only_when_githubs_record_meets_the_orders_terms(tmp_path):
    w = ordered(tmp_path)
    w.chain.bind(MONA)
    code, run, text = attested(w, "pay")
    assert code == 1 and w.signer.asked == [] and "nothing was signed. Pull request #12 of o/r is not merged." in text
    w.hub.merge(12)
    head = w.hub.pulls[12]["head"]["sha"]
    w.hub.checks[head] = [check("test", "failure"), check("build")]
    code, run, text = attested(w, "pay")
    assert code == 1 and w.signer.asked == [] and "does not take the work order on o/r issue #7 (20.00 test USDC) as GitHub's record stands." in text
    assert "What was found:\n- pull request #12 by @mona, merged on " in text and "- `test`: failed" in text
    w.hub.checks[head] = [check("test"), check("build")]
    code, run, text = attested(w, "pay", payees="666.10000.-")
    assert code == 1 and "is not who GitHub's record says is paid (4242.10000.-). Leave it empty." in text and w.signer.asked == []
    code, run, text = attested(w, "pay")                                                    # World relays with its own key
    assert code == 0 and w.signer.asked == [pay.order_pay_audience(ORDER, head, pay.terms_hash(terms.canonical(BOUGHT)), pay.MERGE, 12, [(MONA["id"], 10_000, None)])]
    assert "GitHub signed that pull request #12 takes the work order on o/r issue #7 (20.00 test USDC): it pays @mona, and Solana took it" in text
    assert w.chain.orders() == [] and run.outputs["audience"] == w.signer.asked[0] and set(w.screened) == {WALLET}
    code, run, text = attested(w, "pay")
    assert code == 1 and f"No work order is at `{ORDER}` on Solana (devnet): it was paid, refunded, or never there." in text
    # without a relay key the token is the job's output, for any relayer
    w = ordered(tmp_path / "nokey")
    w.env.pop("KNOS_RELAY_KEY")
    w.chain.bind(MONA)
    w.hub.merge(12)
    code, run, text = attested(w, "pay")
    assert code == 0 and run.outputs["token"].count(".") == 2 and "The signed token is above (`token=`)" in text and len(w.chain.orders()) == 1
    # an order funded with `neutral off` is its own repository's to sign, and a pull request that closes another issue takes nothing
    w = ordered(tmp_path / "off", flags=pay.F_FAUCET)
    w.chain.bind(MONA)
    w.hub.merge(12)
    code, run, text = attested(w, "pay")
    assert code == 1 and "This work order was funded with `neutral off`: only o/r's own workflow can sign for it." in text
    assert attested(w, "pay", GITHUB_REPOSITORY_ID=str(REPO_ID))[0] == 0
    w = ordered(tmp_path / "other")
    w.hub.pull(13, MONA, "Fixes #8")
    w.hub.merge(13)
    code, run, text = attested(w, "pay", 13)
    assert code == 1 and "pull request #13 does not close issue #7" in text and w.signer.asked == []
    assert attested(w, "pay", order="not-an-address")[2].startswith("Knos attest: nothing was signed. `not-an-address` is not a Solana address")


def test_attest_takes_reverts_and_rules_each_on_what_the_public_record_and_the_order_say(tmp_path):
    w = ordered(tmp_path, reserve_days=7, arbiter=ERIN["id"])
    code, run, text = attested(w, "take", None)
    assert code == 0 and w.signer.asked == [f"knos3:take:{ORDER}:{MONA['id']}:7"] and w.chain.orders(7)[0][1].reserved_by == MONA["id"]
    code, run, text = attested(w, "take", None, who_=EVE)
    assert code == 1 and f"is reserved for GitHub user id {MONA['id']} until " in text
    # the arbiter rules, and nobody else
    code, run, text = attested(w, "rule", None, f"{MONA['id']}.10000.-")
    assert code == 1 and "names GitHub user id 77 as its arbiter, and this run was started by @mona (id 4242)." in text
    code, run, text = attested(w, "rule", None, "4242.6000.-", who_=ERIN)
    assert code == 1 and "`--payees` is one to four entries `id.bps.address`" in text
    w.env.pop("KNOS_RELAY_KEY")
    code, run, text = attested(w, "rule", None, f"{MONA['id']}.6000.-,{EVE['id']}.4000.{ADDRESS}", who_=ERIN)
    assert code == 0 and w.signer.asked[-1] == f"knos3:rule:{ORDER}:{MONA['id']}.6000.-,{EVE['id']}.4000.{ADDRESS}"
    # a revert: only inside the warranty, and only when the default branch's history holds one
    w = ordered(tmp_path / "revert", holdback_bps=2000, warranty_days=14)
    w.env.pop("KNOS_RELAY_KEY")
    w.hub.merge(12)
    assert "holds nothing back now: a revert counts only inside its warranty" in attested(w, "revert")[2]
    held = bytearray(w.chain.accounts[str(ORDER)])
    held[1] = 4
    w.chain.accounts[str(ORDER)] = bytes(held)
    merge = w.hub.pulls[12]["merge_commit_sha"]
    w.hub.history = [{"sha": "f" * 40, "commit": {"message": "Tidy the docs"}}, {"sha": merge, "commit": {"message": "Fix slugify (#12)"}}]
    code, run, text = attested(w, "revert")
    assert code == 1 and f"No commit on `main` of o/r since the merge reverts pull request #12's merge commit `{merge[:7]}` (2 looked at)" in text
    w.hub.history.insert(0, {"sha": "e" * 40, "commit": {"message": f'Revert "Fix slugify (#12)"\n\nThis reverts commit {merge}.'}})
    code, run, text = attested(w, "revert")
    assert code == 0 and w.signer.asked == [f"knos3:revert:{ORDER}:{w.hub.pulls[12]['head']['sha']}"]
    assert f"commit `eeeeeee` on `main` reverts pull request #12's merge `{merge[:7]}`" in text


class Sellers:
    """mona/knos-attest on api.github.com beside the World's o/r: its issues and their comments, and nothing else. And
    the public worker's pass over it (the Worker of tests/_flow.py reads o/r alone)."""
    HERE = "mona/knos-attest"

    def __init__(self, w: World, write: bool = True):
        self.w, self.write, self.issues, self.comments, self.asked = w, write, [], [], []

    def __call__(self, path: str, data: dict | None = None, method: str | None = None):
        if not path.startswith(f"repos/{self.HERE}/"):
            return self.w.hub(path, data, method) if method else self.w.hub(path, data)
        self.asked.append((path, data))
        rest = path[len(f"repos/{self.HERE}/"):]
        if data is not None and not self.write:
            raise OSError("403 Resource not accessible by integration")
        if rest.startswith("issues?"):
            return list(self.issues)
        if rest == "issues":
            self.issues.append({"number": len(self.issues) + 1, "title": data["title"], "body": data["body"], "state": "open"})
            return self.issues[-1]
        n = int(rest.split("/")[1])
        self.comments.append({"issue": n, "body": data["body"], "html_url": f"https://github.com/{self.HERE}/issues/{n}#issuecomment-{len(self.comments) + 1}"})
        return self.comments[-1]

    # -- knos.proof.ghrelay, by its contract ---------------------------------------------------------------------------
    def token_id(self, jwt: str) -> str:
        return self.w.worker.token_id(jwt)

    def wait_for(self, tid: str, log_repo: str, timeout: float, every: float = 3.0, get=None) -> str | None:
        import re
        for c in self.comments:
            for marker, jwt in re.findall(r"^knos-(proof|take|revert|rule): (eyJ[\w-]+\.[\w-]+\.[\w-]+)$", c["body"], re.M):
                assert "knosrelay" in c["body"]                 # the word the public worker's search finds
                if self.token_id(jwt) == tid:
                    r = self.w.relay.submit(self.w.chain, None, jwt, None)
                    head = f"knos-relay {marker} {self.HERE}#{c['issue']} {tid}"
                    return f"{head} ok sig={','.join(r['sigs'])} note=relayed by the worker t=12" if r["ok"] else f"{head} fail {r['why']}"
        return None


def test_attest_with_no_relay_key_posts_its_token_on_the_knos_tokens_issue_of_the_repository_it_runs_in(tmp_path):
    """The seller's run has no key and no secret. What GitHub signed goes where any relayer finds it: one comment on
    the issue titled "knos tokens" of the run's own repository, made the first time; the public worker carries it."""
    w = ordered(tmp_path)
    w.env.pop("KNOS_RELAY_KEY")
    w.chain.bind(MONA)
    w.hub.merge(12)

    def attest(sellers: Sellers, **env) -> tuple[int, flow.Run, str]:
        w.tmp.mkdir(parents=True, exist_ok=True)
        summary = w.tmp / f"tokens-{w.runs}.md"
        run = w.run({}, **{"GITHUB_ACTOR_ID": str(MONA["id"]), "GITHUB_ACTOR": "mona", "GITHUB_REPOSITORY_ID": "999", "GITHUB_REPOSITORY": Sellers.HERE,
                           "GITHUB_STEP_SUMMARY": str(summary), **env})
        run._github, run._ghrelay = sellers, sellers
        w.signer.actor = MONA["id"]
        return flow.attest(run, str(ORDER), "pay", 12), run, summary.read_text(encoding="utf-8")
    # a job whose token cannot write issues: the token is still the job's output, and the run says what it lacks
    code, run, text = attest(Sellers(w, write=False))
    assert code == 1 and run.outputs["token"].count(".") == 2 and "comment" not in run.outputs and len(w.chain.orders()) == 1
    assert 'the token could not be posted for a relayer on the "knos tokens" issue of mona/knos-attest' in text and "the job needs `issues: write` there" in text
    sellers = Sellers(w)
    code, run, text = attest(sellers)
    link = "https://github.com/mona/knos-attest/issues/1#issuecomment-1"
    assert code == 0 and [(i["number"], i["title"]) for i in sellers.issues] == [(1, "knos tokens")] and w.chain.orders() == []
    assert sellers.comments[0]["body"].startswith(f"knos-proof: {run.outputs['token']}\n") and run.outputs["comment"] == link
    assert "and Solana took it" in text and f"The token is posted for any relayer at {link}." in text
    # the next run posts on the same issue: it is made once
    w2 = ordered(tmp_path / "again")
    w2.env.pop("KNOS_RELAY_KEY")
    sellers.w = w2
    w2.chain.bind(MONA)
    w2.hub.merge(12)
    w = w2
    code, run, text = attest(sellers)
    assert code == 0 and len(sellers.issues) == 1 and [c["issue"] for c in sellers.comments] == [1, 1] and run.outputs["comment"].endswith("#issuecomment-2")


def test_settle_neutral_starts_the_attest_workflow_in_the_sellers_own_repository_through_gh(tmp_path, capsys):
    w = ordered(tmp_path)
    w.hub.merge(12)
    calls = []

    def gh(*args: str) -> str:
        calls.append(args)
        return "mona\n" if args[:2] == ("api", "user") else ""
    run = w.run({})
    run._gh = gh
    assert flow.neutral(run, "https://github.com/o/r/pull/12") == 0
    assert calls[1] == ("workflow", "run", "knos-attest.yml", "--repo", "mona/knos-attest", "-f", "repository=o/r", "-f", "pull=12", "-f",
                        f"order={ORDER}", "-f", "kind=pay")
    said = capsys.readouterr().out
    assert f"Started `knos attest` in mona/knos-attest for the work order on issue #7 (20.00 test USDC) of o/r, order {ORDER}." in said
    assert 'The run posts its signed token as a comment on the issue titled "knos tokens" of mona/knos-attest: https://github.com/mona/knos-attest/issues' in said
    # with a GitHub CLI that answers: the "knos tokens" issue is found (or made), and the comment the run posts is waited for and linked
    import base64
    import json
    token = "eyJhbGciOiJSUzI1NiJ9." + base64.urlsafe_b64encode(json.dumps({"aud": f"knos3:pay:{ORDER}:{'a' * 40}:{'0' * 64}:0:12:4242.10000.-"}).encode()).decode().rstrip("=") + ".c2ln"
    polls = []

    def cli(*args: str) -> str:
        path = next((a for a in args[1:] if a.startswith("repos/")), "")
        if args[:2] == ("api", "user"):
            return "mona\n"
        if "/comments" in path:
            polls.append(path)
            mine = {"body": f"knos-proof: {token}\n\n<sub>knosrelay</sub>", "html_url": "https://github.com/mona/knos-attest/issues/3#issuecomment-9"}
            return json.dumps([{"body": "knos-proof: eyJhbGciOiJSUzI1NiJ9.e30.c2ln", "html_url": "x"}] + ([mine] if len(polls) > 2 else []))
        if path.endswith("/issues") and "POST" in args:
            assert ("-f", "title=knos tokens") == args[args.index("title=knos tokens") - 1:args.index("title=knos tokens") + 1]
            return json.dumps({"number": 3})
        return "[]" if "/issues?" in path else ""
    run = w.run({})
    run._gh = cli
    t0 = w.clock()
    assert flow.neutral(run, "https://github.com/o/r/pull/12") == 0
    said = capsys.readouterr().out
    assert f"The signed token for order {ORDER} is posted at https://github.com/mona/knos-attest/issues/3#issuecomment-9." in said
    assert len(polls) == 3 and w.clock() - t0 == 2 * flow.NEUTRAL_EVERY and "/issues/3/comments?since=" in polls[0]
    polls.clear()
    polls += ["never", "never", "never"]
    token = "eyJhbGciOiJSUzI1NiJ9.e30.c2ln"                                # the run signed nothing for this order: said after three minutes, with where it would be
    t0 = w.clock()
    assert flow.neutral(run, "https://github.com/o/r/pull/12") == 0 and w.clock() - t0 >= flow.NEUTRAL_WAIT
    assert "has not posted its signed token" in capsys.readouterr().out

    def no_gh(*args: str) -> str:
        raise OSError("gh: command not found")
    run = w.run({})
    run._gh = no_gh
    assert flow.neutral(run, "https://github.com/o/r/pull/12") == 1
    assert "The attest workflow was not started: gh: command not found. It needs the GitHub CLI signed in" in capsys.readouterr().out
    w.version = 0
    assert flow.neutral(w.run({}), "https://github.com/o/r/pull/12") == 1 and "no open work order waits there" in capsys.readouterr().out
    # the command line: attest's own options, and --neutral alone
    assert flow.takes(["attest", "--help"]) and flow.main(["attest", "--help"]) == 0 and "--repository" in capsys.readouterr().out
    assert flow.main(["settle", "--neutral", "o/r#12"]) == 1
    assert capsys.readouterr().out.strip() == "--neutral takes a pull request's URL, like https://github.com/owner/name/pull/7, and nothing else."
    assert flow.main(["attest", "--repository", "o/r", "--order", "x", "--kind", "bless"]) == 2


# ---- knos canary ------------------------------------------------------------------------------------------------------

class Round:
    """api.github.com as the canary uses it, with Knos answering after `fund` and `pay` seconds and the pull
    request's check finishing after `check`."""

    def __init__(self, clock, fund: float = 40, check: float = 25, pay: float = 20, conclusion: str = "success", paid: str = "Knos: paid. @canary received 5.00 test USDC"):
        self.clock, self.fund, self.check, self.pay, self.conclusion, self.paid = clock, fund, check, pay, conclusion, paid
        self.calls, self.at = [], {}

    def __call__(self, method: str, path: str, data: dict | None = None):
        self.calls.append((method, path.split("?")[0]))
        now, bare = self.clock(), path.split("?")[0]
        if (method, bare) == ("POST", "repos/o/r/issues"):
            assert data["body"].splitlines()[-1].startswith("/knos fund 5")
            self.at["issue"] = now
            return {"number": 30}
        if bare == "repos/o/r/issues/30/comments":
            return [{"body": "Thanks!"}, *([{"body": "Knos: 5.00 test USDC from the devnet faucet is in escrow for issue #30"}] if now - self.at["issue"] >= self.fund else [])]
        if bare == "repos/o/r":
            return {"default_branch": "main"}
        if bare == "repos/o/r/git/ref/heads/main":
            return {"object": {"sha": "a" * 40}}
        if (method, bare) == ("POST", "repos/o/r/pulls"):
            assert data["body"] == "Fixes #30" and data["base"] == "main"
            self.at["pull"] = now
            return {"number": 31, "head": {"sha": "b" * 40}}
        if bare == f"repos/o/r/commits/{'b' * 40}/check-runs":
            finished = now - self.at["pull"] >= self.check
            return {"check_runs": [{"name": "knos check", "status": "completed" if finished else "in_progress", "conclusion": self.conclusion if finished else None}]}
        if (method, bare) == ("PUT", "repos/o/r/pulls/31/merge"):
            self.at["merge"] = now
            return {"merged": True}
        if bare == "repos/o/r/issues/31/comments":
            return [{"body": self.paid}] if self.pay is not None and now - self.at["merge"] >= self.pay else []
        return {}


def test_the_canary_times_each_leg_of_one_round_and_fails_when_the_payment_does_not_land_in_five_minutes(tmp_path, capsys):
    import json
    w = World(tmp_path)
    run = w.run({}, GITHUB_OUTPUT=str(tmp_path / "out.txt"))
    api = Round(w.clock)
    assert flow.canary(run, api) == 0
    out = capsys.readouterr().out
    line = json.loads(out.split("knos-canary ", 1)[1].splitlines()[0])
    assert {k: line[k] for k in ("ok", *flow.LEGS)} == {"ok": True, "fund": 40, "open": 0, "check": 25, "merge": 0, "pay": 20} and line["repo"] == "o/r"
    assert "Knos canary: paid. fund 40 s, open 0 s, check 25 s, merge 0 s, pay 20 s." in out
    assert "fund_seconds=40\n" in (tmp_path / "out.txt").read_text(encoding="utf-8") and run.outputs["pay_seconds"] == "20"
    assert [c for c in api.calls if c[0] != "GET"] == [("POST", "repos/o/r/issues"), ("POST", "repos/o/r/git/refs"),
                                                        ("PUT", f"repos/o/r/contents/canary/{line['at']}.txt"), ("POST", "repos/o/r/pulls"), ("PUT", "repos/o/r/pulls/31/merge")]
    # no payment within five minutes of the merge: exit 1, and the line says which leg and why
    w = World(tmp_path / "late")
    assert flow.canary(w.run({}), Round(w.clock, pay=None)) == 1
    out = capsys.readouterr().out
    line = json.loads(out.split("knos-canary ", 1)[1].splitlines()[0])
    assert (line["ok"], line["failed"], line["pay"]) == (False, "pay", 300) and "did not land within 5 minutes of the merge" in line["why"]
    assert "Knos canary: the `pay` leg failed: the payment for pull request #31 did not land within 5 minutes of the merge" in out
    # a check that fails, a funding that is refused, and Knos saying it did not pay are each their own failure
    for arrange, leg, why in ((dict(conclusion="failure"), "check", "`knos check` did not pass on pull request #31"),
                              (dict(paid="Knos: not paid. This pull request does not take the bounty"), "pay", "Knos: not paid."),
                              (dict(fund=10_000), "fund", "no reply from Knos on issue #30 within 300 s")):
        w = World(tmp_path / leg)
        assert flow.canary(w.run({}), Round(w.clock, **arrange)) == 1
        line = json.loads(capsys.readouterr().out.split("knos-canary ", 1)[1].splitlines()[0])
        assert line["failed"] == leg and why in line["why"], line
    assert flow.main(["canary", "--repo", "nowhere"]) == 1 and "Name the repository as owner/name" in capsys.readouterr().out


# ---- what settlements teach, and what a funding proposes from it ------------------------------------------------------

def merged_round(w: World, issue: int, number: int, files: list[str], checks: list, paid_to: dict = MONA) -> str:
    """Fund `issue`, open pull request `number` that changes `files` with `checks` on its last commit, merge and settle."""
    w.hub.issue(issue)
    w.chain.fund(issue, 5_000_000, BOUGHT)
    w.clock.sleep(3600)
    head = w.hub.pull(number, paid_to, f"Fixes #{issue}")["head"]["sha"]
    w.hub.files[number] = [{"filename": f, "patch": "@@ -1 +1 @@\n+x"} for f in files]
    w.hub.checks[head] = checks
    flow.settle(w.run(w.hub.merge(number)))
    return w.hub.knos(number)[-1]


def test_every_settlement_is_remembered_and_a_funding_proposes_terms_from_that_memory_and_nothing_from_an_empty_one(tmp_path):
    from knos.proof import memory
    w = World(tmp_path)
    w.hub.required = [{"context": "test", "integration_id": 15368}]
    w.chain.bind(MONA)
    w.hub.issue(7)
    empty = {"checks": [], "paths": [], "said": []}
    assert flow.suggest_terms("o/r", []) == empty == flow.suggest_terms("o/r", flow._Kept())
    assert flow.command(w.run(w.hub.commented(7, HUBOT, "/knos fund 20"))) == 0
    assert "Knos remembers" not in w.hub.knos(7)[-1] and "add it?" not in w.hub.knos(7)[-1]          # nothing is proposed from an empty memory
    # three merged changes under src/parser were paid while `fuzz` failed on them (the bounty did not ask for it); one refused
    for k in range(3):
        assert merged_round(w, 20 + k, 40 + k, ["src/parser/lex.py", "src/parser/ast/node.py"], [check("test"), check("build"), check("fuzz", "failure")]).startswith("Knos: paid.")
    assert merged_round(w, 30, 50, ["src/cli.py"], [check("test", "failure"), check("build")]).startswith("Knos: not paid.")
    n, lessons = memory.read("o/r", w.hub)
    kept = [x["body"] for x in lessons if x["category"] == "settlement"]
    assert len(kept) == 4 and [b["paid"] for b in kept].count(True) == 3
    paid = next(b for b in kept if b["pull"] == 40)
    assert paid["failed"] == {"fuzz": ["src/parser"]} and paid["met"] == ["build", "test"] and paid["paths"] == ["src/parser"]      # two levels deep
    assert next(b for b in kept if b["pull"] == 50)["failed"] == {"test": ["src"]}
    got = flow.suggest_terms("o/r", lessons)                                               # the rows themselves: what knos_quote hands in
    assert got["checks"] == ["fuzz", "test"] and got["paths"] == ["src/**"]
    assert got["said"][0] == "In this repository, changes under `src/parser` failed `fuzz` 3 times: add it? (`checks: fuzz` on the fund line.)"
    assert flow.suggest_terms("someone/else", lessons) == empty                            # a repository's memory is its own
    # the next funding that names no checks is told; one that names them is not told about checks
    w.hub.issue(8)
    assert flow.command(w.run(w.hub.commented(8, HUBOT, "/knos fund 20"))) == 0
    reply = plain(w.hub.knos(8)[-1], 1600)
    assert ("\n\nFrom what Knos remembers of this repository: In this repository, changes under `src/parser` failed `fuzz` 3 times: add it? "
            "(`checks: fuzz` on the fund line.) The 3 changes paid here so far stayed under `src/**`: keep this one there too? (`paths: src/**` on "
            "the fund line.) To change the terms, fund another issue with them: these are fixed.") in reply
    assert "failed `test`" not in reply                                                    # `test` is in this bounty's terms already
    w.hub.issue(9)
    assert flow.command(w.run(w.hub.commented(9, HUBOT, "/knos fund 20 checks: test, fuzz paths: src/**"))) == 0
    assert "Knos remembers" not in w.hub.knos(9)[-1]
    # the proposal changes with what memory holds: two more settlements where `lint` failed under docs
    for k in range(4):
        merged_round(w, 60 + k, 70 + k, ["docs/guide/a.md"], [check("test"), check("build"), check("lint", "failure")])
    got = flow.suggest_terms("o/r", memory.read("o/r", w.hub)[1])
    assert got["checks"] == ["lint", "fuzz"] and got["said"][0].startswith("In this repository, changes under `docs/guide` failed `lint` 4 times") and got["paths"] == ["docs/**", "src/**"]
    # the same with a work order's reply
    w.version = 1
    w.hub.issue(10)
    assert flow.command(w.run(w.hub.commented(10, HUBOT, "/knos fund 20"))) == 0
    assert "changes under `docs/guide` failed `lint` 4 times: add it?" in w.hub.knos(10)[-1] and "as a work order" in w.hub.knos(10)[-1]
