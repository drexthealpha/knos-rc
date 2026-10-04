// Get paid with no wallet app: a passkey (WebAuthn, P-256) is the key of a wallet that is a program-derived address of
// knos_passkey (sdk/settle/passkey.js). The page makes it, shows its address and balance, and signs a withdrawal; a
// withdrawal is carried to Solana by the public relay, from a line posted in the person's own knos-claim repository.
// This page sends nothing: it shows the line and a link to GitHub's new-issue page with the line in it.
// Only the credential's id and the public key are kept, in localStorage, only for the person's convenience: every read
// and write is guarded, and with no storage the page works the same (the details can be copied out and typed back in).
import * as passkey from "./passkey.js";

const STORE = "knos-passkey";
const LOGIN = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;       // a GitHub login: 1 to 39, letters, digits, single hyphens inside

const b64 = (u8) => btoa(String.fromCharCode(...u8));
const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, "0")).join("");
const unhex = (s) => (/^([0-9a-f]{2})+$/i.test(s) ? Uint8Array.from(s.match(/../g), (h) => parseInt(h, 16)) : new Uint8Array(0));

// The request is the one line the relay reads: `knos-withdraw: ` and the base64 of the key, the mint, the destination,
// the amount, the withdrawal's number and the passkey's signature over them. Its format is written once, in passkey.js
// (withdrawLine, readWithdrawRequest), where the fixture the relay's own reader is tested against holds it to its
// bytes. This page shows the line withdrawLine makes, and never reads one back.

export function initClaim(ctx) {
  const { $, esc, knos, RPC, EXPLORER, units, say, money } = ctx;
  const rpId = location.hostname, MINT = knos.USDC_DEVNET;
  $("pk-rp").textContent = rpId;
  let me = null;                 // { credentialId: Uint8Array | null, key: Uint8Array(33), wallet: address, balance, nonce }

  // the details a person keeps, in this browser if it lets the page, and as one line of text they can hold themselves
  const remember = () => { try { localStorage.setItem(STORE, JSON.stringify({ credentialId: me.credentialId ? b64(me.credentialId) : null, key: hex(me.key) })); } catch { /* no storage: the page works the same */ } };
  const recall = () => { try { return JSON.parse(localStorage.getItem(STORE) || "null"); } catch { return null; } };
  const exportText = () => JSON.stringify({ credentialId: me.credentialId ? b64(me.credentialId) : null, key: hex(me.key), wallet: me.wallet });
  async function adopt(details) {
    if (!details || typeof details !== "object") throw new Error("not details");
    const key = passkey.compressed(unhex(String(details.key || "")));
    const credentialId = details.credentialId ? unb64(String(details.credentialId)) : null;
    const wallet = await passkey.wallet(key);
    if (details.wallet !== undefined && details.wallet !== wallet) throw new Error("the address does not belong to that key");
    me = { credentialId, key, wallet };
  }

  async function show() {
    $("pk-wallet").hidden = false;
    $("pk-address").textContent = me.wallet;
    $("pk-address-comment").textContent = `/knos address ${me.wallet}`;
    $("pk-export").value = exportText();
    $("pk-request").innerHTML = "";
    await balance();
  }

  // what the wallet holds of the test token, and its next withdrawal number: the wallet's account holds the last one used
  async function read() {
    await ctx.devnet();
    const mine = await passkey.ata(me.wallet, MINT);
    const [held, opened] = await Promise.all([knos.account(RPC, mine), knos.accountInfo(RPC, me.wallet)]);
    const token = knos.readTokenAccount(held);
    me.balance = token && token.mint === MINT ? token.amount : 0;
    me.nonce = (opened?.owner === passkey.PASSKEY ? passkey.readWallet(opened.data)?.nonce : 0) ?? 0;
    return { mine, has: Boolean(token) };
  }
  async function balance() {
    const out = $("pk-balance");
    try {
      const { mine, has } = await read();
      out.innerHTML = `<p class="status" id="pk-held">${has ? `The wallet holds <strong>${esc(money(me.balance))}</strong> test USDC` : "The wallet holds no test USDC yet: it has no token account"}, at
        <a class="mono" href="${esc(EXPLORER("address", mine))}" target="_blank" rel="noopener">${esc(mine.slice(0, 4))}…${esc(mine.slice(-4))}</a>. Withdrawals so far: ${esc(String(me.nonce))}.</p>`;
    } catch (e) { out.innerHTML = `<p class="status bad">Could not read devnet just now: ${esc(e.message)}</p>`; }
  }

  $("pk-create").onclick = async () => {
    const st = $("pk-status");
    try {
      say(st, "Asking your device to make a passkey…");
      const made = await passkey.create({ rpId, rpName: "Knos", userName: "Knos wallet" });
      me = { credentialId: made.credentialId, key: made.key, wallet: made.wallet };
      remember();
      st.innerHTML = `<p class="status ok" id="pk-made">Your passkey wallet is made. Its address is below.</p>`;
      await show();
    } catch (e) {
      const ip = /^\d+\.\d+\.\d+\.\d+$|^\[/.test(rpId);
      say(st, `${esc(e.message || "The passkey was not made")}. ${ip ? "Passkeys need the site's own address, not an IP number. " : ""}Nothing was created.`, "bad");
    }
  };
  $("pk-refresh").onclick = () => balance();
  $("pk-use").onclick = async () => {
    const st = $("pk-status");
    try { await adopt(JSON.parse($("pk-import").value)); remember(); st.innerHTML = `<p class="status ok" id="pk-made">Those details are in use.</p>`; await show(); }
    catch { say(st, "Those are not a passkey wallet's details. Paste exactly what “The details” showed: a line starting with a brace.", "bad"); }
  };

  $("pk-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const out = $("pk-request"), bad = (words) => say(out, words, "bad");
    if (!me) return bad("Make a passkey wallet, or enter the details of one, first.");
    const amount = units($("pk-amount").value), dest = $("pk-dest").value.trim(), login = $("pk-login").value.trim().replace(/^@/, "");
    if (amount === null || amount <= 0) return bad("The amount is a number like 5 or 2.50, with at most six decimals.");
    try { passkey.unb58(dest); if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(dest)) throw new Error("length"); } catch { return bad("The destination is a Solana address: 32 to 44 letters and digits, as a wallet shows it."); }
    if (dest === me.wallet) return bad("The destination is this wallet itself. Name another address.");
    if (!LOGIN.test(login)) return bad("Your GitHub login is letters, digits and single hyphens, up to 39.");
    try {
      say(out, "Reading devnet…");
      await read();
      if (amount > me.balance) return bad(`The wallet holds ${esc(money(me.balance))} test USDC, so ${esc(money(amount))} cannot be withdrawn. Nothing was signed.`);
      const to = await passkey.ata(dest, MINT), there = await knos.accountInfo(RPC, to), token = there?.owner === knos.TOKEN ? knos.readTokenAccount(there.data) : null;
      if (!token || token.mint !== MINT) return bad(`${esc(dest.slice(0, 4))}…${esc(dest.slice(-4))} has no test USDC token account on devnet, so there is nowhere for the money to land. Make one there first (any wallet that has received test USDC has one). Nothing was signed.`);
      const nonce = me.nonce + 1;
      say(out, "Asking your device to sign this withdrawal…");
      const assertion = await passkey.sign(await passkey.challenge(me.wallet, MINT, to, amount, nonce), { rpId, credentialId: me.credentialId });
      const comment = passkey.withdrawLine({ key: me.key, mint: MINT, to, amount, nonce, assertion });
      const link = `https://github.com/${login}/knos-claim/issues/new?title=${encodeURIComponent("knos withdraw")}&body=${encodeURIComponent(comment)}`;
      out.innerHTML = `<p class="status ok" id="pk-signed">Signed on your device. Nothing has been sent.</p>
        <p>The signature is for this and nothing else: <strong>${esc(money(amount))} test USDC</strong> from your wallet to the token account of
          <span class="mono">${esc(dest)}</span>, as withdrawal number ${esc(String(nonce))}. It cannot be used for another amount, another destination or a second time.</p>
        <label for="pk-comment">The request, one line</label>
        <textarea id="pk-comment" readonly rows="6" spellcheck="false">${esc(comment)}</textarea>
        <p><button type="button" id="pk-copy" class="ghost small">Copy the line</button>
          <a id="pk-open" class="button" href="${esc(link)}" target="_blank" rel="noopener">Open GitHub with it in a new issue</a></p>
        <ol class="steps">
          <li>Post the line in your repository <span class="mono">${esc(login)}/knos-claim</span> (the one the claim workflow runs in): the button opens GitHub's
            new-issue page there with the line already in it. GitHub fills a new issue from a link but cannot fill a comment box, so paste the line yourself to comment instead.
            Press the green button on GitHub yourself: this page cannot.</li>
          <li>The public relay reads the line, and anyone may send it: the relay pays the transaction fee, so you need no SOL. The wallet's next withdrawal number is then ${esc(String(nonce + 1))}.</li>
          <li>Press “Check the balance” in a minute or two. Sign one withdrawal at a time: a second one signed before the first is sent has the same number, and only one of them can be.</li>
        </ol>`;
      const copy = $("pk-copy");
      copy.onclick = async () => { try { await navigator.clipboard.writeText(comment); copy.textContent = "Copied"; } catch { $("pk-comment").select(); copy.textContent = "Select and copy it by hand"; } };
    } catch (e) {
      const refused = /NotAllowed|AbortError|cancel/i.test(`${e.name} ${e.message}`);
      bad(refused ? "Signing was cancelled or the device said no, so nothing was signed. Press the button to try again." : esc(e.message || "That did not work."));
    }
  };

  const saved = recall();
  if (saved) adopt(saved).then(() => { $("pk-status").innerHTML = `<p class="status" id="pk-made">The passkey wallet this browser kept is in use.</p>`; return show(); }).catch(() => {});
}
