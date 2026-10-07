// "For judges" (#judges): the rows of docs/JUDGES.md, as judges.json holds them (scripts/judges.py; copied by
// scripts/build_site.sh): { rows: [{ thing, sentence, link, label }], not_real: [..] }. One row a judged thing, one
// sentence, one link a judge can open; then what is not real yet. The page words nothing of its own.
const DOC = "https://github.com/drexthealpha/Knos/blob/main/docs/JUDGES.md";
export function renderJudges(el, { esc, data } = {}) {
  const rows = Array.isArray(data?.rows) ? data.rows.filter((r) => r && (r.thing || r.sentence)) : [];
  if (!el || !rows.length) return;
  const zeros = Array.isArray(data.not_real) ? data.not_real : [];
  el.innerHTML = `<h2>For judges: one page</h2>
    <p class="lede">One row a judged thing. One link each.</p>
    <div class="card" data-keep><div class="k-table"><table>
      <thead><tr><th scope="col">Judged</th><th scope="col">What exists</th><th scope="col">Evidence</th></tr></thead>
      <tbody>${rows.map((r) => `<tr><th scope="row">${esc(r.thing || "")}</th><td>${esc(r.sentence || "")}</td>
        <td>${r.link ? `<a href="${esc(r.link)}"${/^https?:/.test(r.link) ? ' target="_blank" rel="noopener"' : ""}>${esc(r.label || "Open")}</a>` : ""}</td></tr>`).join("")}</tbody>
    </table></div></div>
    ${zeros.length ? `<h3 id="judges-zeros">Not real yet</h3><div class="k-table" data-keep><table><tbody>${zeros.map((z) => `<tr><td>${esc(z)}</td></tr>`).join("")}</tbody></table></div>` : ""}
    <p class="fine" data-keep><a id="judges-doc" href="${DOC}">The same page as a document</a></p>`;
}
