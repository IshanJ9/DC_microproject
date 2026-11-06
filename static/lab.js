/* Hashing lab and Database tabs. Loaded after app.js, which provides $, S, col, esc, ringSVG, gridHTML, setHTML, get, poll. */

const send = (url, body) => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json());
const tag = (n, sm = true) => `<span class="nd ${sm ? "sm" : ""}" style="--c:${col(n)}">${n}</span>`;

let trace = null, steps = [], shown = 0;
let labMode = "live";
let sim = null, simView = "before";
let labReady = false, dbReady = false;
let dbRows = [], editingId = null, hotId = null;

/* ---------------- follow one key ---------------- */
function buildSteps(T) {
  const h = T.hash, dec = BigInt("0x" + h.first8).toString();
  const out = [];
  out.push({ stage: 0, title: "Start with the key",
    body: `<span class="hash">${esc(T.key)}</span> is just text. The ring has positions, not names, so the first job is to turn the key into a number.` });
  out.push({ stage: 0, title: "Hash it with MD5",
    body: `MD5 of the key is <span class="hash">${h.md5}</span>. Keep the first 8 bytes, <span class="hash">${h.first8}</span>, which is the number <b>${dec}</b>. The same key always gives the same number.` });
  out.push({ stage: 1, title: "Turn the number into a spot on the ring",
    body: `${dec} divided by 2<sup>64</sup> is <b>${h.fraction.toFixed(4)}</b> of the way round the circle, which is <b>${h.angle}°</b> (0° is the top, angles go clockwise). The white dot is the key.` });
  if (!T.walk.length) {
    out.push({ stage: 1, title: "No servers", body: "There are no servers in the ring, so nothing can own this key." });
    return out;
  }
  const first = T.walk[0], newHops = T.walk.filter(x => x.new);
  out.push({ stage: 2, title: "Walk clockwise to the first virtual node",
    body: `From ${h.angle}° the first virtual node you meet is <b>${first.label}</b> at ${first.angle}°. It belongs to server ${tag(first.node)}, so <b>server ${first.node} owns this key</b>. The ring has ${T.vnode_count} virtual nodes (${T.servers} servers × ${T.vnodes} each).` });
  if (newHops.length > 1) {
    const rep = newHops[1], skipped = T.walk.slice(1).filter(x => !x.new);
    out.push({ stage: 3, title: "Keep walking to find the spare copy",
      body: (skipped.length ? `${skipped.map(x => x.label).join(", ")} ${skipped.length > 1 ? "belong" : "belongs"} to the same server, so ${skipped.length > 1 ? "they are" : "it is"} skipped. ` : "") +
        `<b>${rep.label}</b> at ${rep.angle}° belongs to ${tag(rep.node)}, a different server. That server holds the <b>replica</b>.` });
  } else {
    out.push({ stage: 2, title: "No spare copy possible", body: "Only one server is in the ring, so there is nowhere to keep a replica." });
  }
  if (T.events.length) {
    const probes = T.events.filter(e => e.type === "probe");
    const label = { hit: "has it", miss: "does not have it", down: "did not answer" };
    const anyHit = probes.some(p => p.result === "hit");
    out.push({ stage: 3, title: "Ask the cache servers",
      body: probes.map(p => `<span class="res ${p.result}">Server ${p.node} ${label[p.result]}</span>`).join(" ") +
        (anyHit ? " The cache answers, so <b>the database is not touched</b>." : " That is a cache <b>miss</b>.") });
    const db = T.events.find(e => e.type === "db");
    if (db) {
      out.push({ stage: 3, title: "Query the database",
        body: `The coordinator runs this SQL (${db.ms} ms):<code class="sqlbox">${esc(db.sql)}</code>` });
      const st = T.events.find(e => e.type === "store");
      out.push({ stage: 3, title: "Store a copy in the cache",
        body: `The row is saved on ${(st ? st.nodes : []).map(n => tag(n)).join(" ") || "no server"}. Press <b>Start</b> again to see the same key come back as a hit.` });
    }
    out.push({ stage: 3, title: "Return the row",
      body: `<b>${esc(T.product.name)}</b>, $${T.product.price.toFixed(2)}, ${T.source === "cache" ? "from the cache" : "from the database"} in ${T.ms} ms.` });
  } else {
    out.push({ stage: 3, title: "Done",
      body: `This key is not a product row, so there is no cache or database step. The hashing steps work the same for any key. Try <code>product:7</code> for the full journey.` });
  }
  return out;
}

function renderTrace() {
  $("traceSteps").innerHTML = steps.map((s, i) =>
    `<li class="${i < shown ? "on" : ""} ${i === shown - 1 ? "now" : ""}"><span class="mark"></span><h3>${s.title}</h3><div class="body">${s.body}</div></li>`).join("");
  $("tracePrev").classList.toggle("busy", shown <= 1);
  $("traceNext").textContent = shown >= steps.length ? "Finished" : `Next step (${shown} of ${steps.length})`;
  $("traceNext").classList.toggle("busy", shown >= steps.length);
  drawLabRing();
}

async function startTrace(e) {
  if (e) e.preventDefault();
  const key = $("traceInput").value.trim();
  if (!key) { $("traceSteps").innerHTML = `<li class="on"><span class="mark"></span><h3>Enter a key</h3><div class="body">Type a key such as product:42.</div></li>`; return; }
  trace = await get("/api/trace?key=" + encodeURIComponent(key));
  if (trace.error) return;
  steps = buildSteps(trace); shown = 1; labMode = "trace";
  $("simView").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === "before"));
  renderTrace();
  poll(true);
}
$("traceForm").addEventListener("submit", startTrace);
$("traceNext").onclick = () => { if (shown < steps.length) { shown++; renderTrace(); } };
$("tracePrev").onclick = () => { if (shown > 1) { shown--; renderTrace(); } };
$("traceAll").onclick = () => { shown = steps.length; renderTrace(); };
$("traceEvict").onclick = async () => {
  const r = await send("/api/evict", { key: $("traceInput").value.trim() });
  $("labCap").textContent = r.removed.length ? `Removed from server ${r.removed.join(" and ")}. Press Start to watch the miss and the database query.` : "That key was not in any cache.";
  poll(true);
};

/* ---------------- ring and caption ---------------- */
function drawLabRing() {
  if (!S) return;
  let svg, cap;
  if (labMode === "sim" && sim) {
    const moved = new Map(sim.moves.map(m => [m.id, m]));
    if (simView === "after") {
      svg = ringSVG(S, { ticks: sim.ticks_after, owners: sim.owners_after, moved, noPulse: true, center: sim.nodes_after.length });
      cap = `After: ${sim.nodes_after.length} servers. White rings mark the ${sim.moves.length} keys that changed owner. Every other dot kept its colour.`;
    } else {
      svg = ringSVG(S, { moved, noPulse: true, center: sim.nodes_before.length });
      cap = `Before: white rings mark the ${sim.moves.length} keys that will change owner. Press After to see where they go.`;
    }
  } else if (labMode === "trace" && trace && steps.length) {
    const stage = Math.max(...steps.slice(0, shown).map(s => s.stage));
    svg = ringSVG(S, { loc: stage >= 1 ? trace : null, stage, noPulse: true });
    cap = `Step ${shown}: ${steps[shown - 1].title}.`;
  } else {
    svg = ringSVG(S, { noPulse: true });
    cap = "The coloured band shows which server owns each stretch of the ring.";
  }
  $("ringLab").innerHTML = svg;
  $("labCap").textContent = cap;
}

/* ---------------- join / leave preview ---------------- */
$("simAction").onchange = () => { $("simNode").hidden = $("simAction").value !== "remove"; };
$("simRun").onclick = async () => {
  $("simErr").textContent = "";
  const action = $("simAction").value;
  const url = `/api/simulate?action=${action}` + (action === "remove" ? `&name=${$("simNode").value}` : "");
  const r = await get(url);
  if (r.error) { $("simErr").textContent = r.error; return; }
  sim = r; labMode = "sim"; simView = "before";
  $("simView").querySelectorAll("button").forEach(b => b.classList.toggle("on", b.dataset.v === "before"));
  const groups = {};
  r.moves.forEach(m => { const k = `${m.from}>${m.to}`; groups[k] = (groups[k] || 0) + 1; });
  const flows = Object.entries(groups).map(([k, n]) => { const [a, b] = k.split(">"); return `${tag(a)} to ${tag(b)}: ${n}`; }).join(" &nbsp; ");
  $("simOut").className = "";
  $("simOut").innerHTML = `<p class="sim-sum">${action === "add" ? `Adding server ${tag(r.node, false)}` : `Removing server ${tag(r.node, false)}`}:
      <b>${r.moves.length} of ${r.total}</b> keys move with consistent hashing, but <b>${r.modulo_ids.length} of ${r.total}</b> would move with plain <code>hash % N</code>.</p>
    <div class="maps"><div><small>Consistent hashing: coloured squares are keys that move</small>${gridHTML(r.moves.map(m => m.id), r.total, "#079455")}</div>
      <div><small>Plain hash % N: coloured squares are keys that move</small>${gridHTML(r.modulo_ids, r.total, "#d92d20")}</div></div>
    <p class="hint">Where the moved keys go: ${flows || "none"}. Nothing else is touched.</p>`;
  drawLabRing();
};
$("simView").addEventListener("click", e => {
  const b = e.target.closest("button[data-v]");
  if (!b || !sim) return;
  simView = b.dataset.v; labMode = "sim";
  $("simView").querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
  drawLabRing();
});

/* ---------------- key map table ---------------- */
$("mapFilter").oninput = () => drawKeyMap();
function drawKeyMap() {
  if (!S) return;
  const q = $("mapFilter").value.trim().toLowerCase();
  const rows = [...S.keys].sort((a, b) => a.angle - b.angle).filter(k =>
    !q || `product:${k.id} ${k.name} ${k.owner} ${k.replicas.join(" ")}`.toLowerCase().includes(q));
  const head = `<tr><th>Ring position</th><th>Key</th><th>Owner</th><th>Replica</th><th>Cached on</th></tr>`;
  const body = rows.map(k => `<tr data-key="product:${k.id}" style="cursor:pointer" title="Click to follow this key">
    <td class="num">${k.angle.toFixed(1)}°</td><td>product:${k.id} <span class="dim">${esc(k.name)}</span></td>
    <td>${k.owner ? tag(k.owner) : "-"}</td><td>${k.replicas.map(n => tag(n)).join("") || "-"}</td>
    <td>${k.holders.map(n => tag(n)).join("") || '<span class="dim">not cached</span>'}</td></tr>`).join("");
  setHTML($("keyMap"), head + (body || `<tr><td colspan="5" class="dim">No keys match.</td></tr>`));
}
$("keyMap").addEventListener("click", e => {
  const tr = e.target.closest("tr[data-key]");
  if (tr) { $("traceInput").value = tr.dataset.key; startTrace(); window.scrollTo({ top: 0, behavior: "smooth" }); }
});

/* ---------------- database tab ---------------- */
async function loadRows() { dbRows = await get("/api/catalog"); }
$("dbFilter").oninput = () => drawDbTable();

function drawDbTable() {
  if (!S || editingId !== null) return;
  const q = $("dbFilter").value.trim().toLowerCase();
  const rows = dbRows.filter(r => !q || `${r.id} ${r.name} ${r.category}`.toLowerCase().includes(q));
  const head = `<tr><th>id</th><th>name</th><th>category</th><th>price</th><th>stock</th><th>cached on</th><th></th></tr>`;
  const body = rows.map(r => {
    const holders = (S.keys[r.id] && S.keys[r.id].holders) || [];
    return `<tr class="${r.id === hotId ? "hot" : ""}"><td class="num">${r.id}</td><td>${esc(r.name)}</td><td class="dim">${r.category}</td>
      <td class="num">$${r.price.toFixed(2)}</td><td class="num">${r.stock}</td>
      <td>${holders.map(n => tag(n)).join("") || '<span class="dim">no</span>'}</td>
      <td class="act"><button class="btn small" data-read="${r.id}">Read</button> <button class="btn small" data-edit="${r.id}" aria-label="Edit price of ${esc(r.name)}">Edit</button></td></tr>`;
  }).join("");
  setHTML($("dbTable"), head + body);
}

function editRow(id) {
  editingId = id;
  const r = dbRows.find(x => x.id === id);
  const tr = $("dbTable").querySelector(`[data-edit="${id}"]`).closest("tr");
  tr.children[3].innerHTML = `$<input class="price" type="number" step="0.01" min="0.01" value="${r.price.toFixed(2)}" aria-label="New price">`;
  tr.children[6].innerHTML = `<button class="btn small primary" data-save="${id}">Save</button> <button class="btn small" data-cancel="1">Cancel</button>`;
  tr.querySelector("input").focus();
}

$("dbTable").addEventListener("click", async e => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.edit) return editRow(+b.dataset.edit);
  if (b.dataset.cancel) { editingId = null; $("dbTable").dataset.h = ""; return drawDbTable(); }
  if (b.dataset.read) {
    const id = +b.dataset.read, r = await get(`/api/product/${id}`);
    hotId = id;
    $("dbNote").innerHTML = r.source === "cache"
      ? `Row ${id} came from the <b>cache</b> (server ${r.node}, ${r.ms} ms). The database was not queried.`
      : `Row ${id} was not cached, so the database ran <code>SELECT</code> (${r.ms} ms) and a copy was stored on its owner and replica. Read it again to see the cache answer.`;
  }
  if (b.dataset.save) {
    const id = +b.dataset.save, price = $("dbTable").querySelector("input.price").value;
    const r = await send("/api/db/price", { id, price });
    if (r.error) { $("dbNote").textContent = r.error; return; }
    editingId = null; hotId = id; await loadRows(); $("dbTable").dataset.h = "";
    $("dbNote").innerHTML = `Ran <code>${esc(r.sql)}</code> (${r.ms} ms). Cached copies on ${r.invalidated.map(n => tag(n)).join(" ") || "no server"} were deleted, so the next read goes to the database and picks up the new price.`;
  }
  await poll(true); await drawSqlLog(); drawDbTable();
});

async function drawSqlLog() {
  const d = await get("/api/db/log");
  setHTML($("sqlLog"), d.log.length
    ? d.log.map(e => `<div><small>${e.t}, ${e.ms} ms, ${esc(e.why)}</small><code>${esc(e.sql)}</code></div>`).join("")
    : `<div class="empty">Nothing yet. Read a row that is not cached.</div>`);
}

function drawDbCounts(m) {
  setHTML($("dbCounts"), `<div><b>${m.hits}</b><small>reads answered by the cache</small></div><div><b>${m.db_queries}</b><small>queries run on the database</small></div>`);
}

/* ---------------- called from the poll loop ---------------- */
async function labTick(s, v) {
  if (v === "lab") {
    if (!labReady) {
      labReady = true;
      $("simNode").innerHTML = s.nodes.filter(n => n.in_ring).map(n => `<option value="${n.name}">Server ${n.name}</option>`).join("");
      startTrace();
    }
    drawLabRing(); drawKeyMap();
    setHTML($("legendLab"), s.nodes.map(n => `<span class="${n.status === "down" ? "down" : ""}" style="--c:${col(n.name)}"><i></i>Server ${n.name}</span>`).join(""));
    const sel = $("simNode"), want = s.nodes.filter(n => n.in_ring).map(n => n.name);
    if (sel.options.length !== want.length) sel.innerHTML = want.map(n => `<option value="${n}">Server ${n}</option>`).join("");
  }
  if (v === "database") {
    if (!dbReady) { dbReady = true; await loadRows(); }
    drawDbTable(); drawDbCounts(s.metrics); drawSqlLog(); drawAccess();
  }
}

/* ---------------- backend visibility: access log and SQL console ---------------- */
const PRESETS = {
  "Hits and misses": "SELECT result, COUNT(*) AS requests, ROUND(AVG(ms), 1) AS avg_ms FROM access_log GROUP BY result",
  "Hits per server": "SELECT server, COUNT(*) AS hits FROM access_log WHERE result = 'hit' GROUP BY server ORDER BY server",
  "Last 10 requests": "SELECT ts, key, result, server, ROUND(ms, 1) AS ms FROM access_log ORDER BY id DESC LIMIT 10",
  "Most requested products": "SELECT p.name, COUNT(*) AS requests FROM access_log a JOIN products p ON a.key = 'product:' || p.id GROUP BY p.id ORDER BY requests DESC LIMIT 10",
  "Rows in products": "SELECT COUNT(*) AS rows FROM products",
  "SQL the app ran": "SELECT ts, ROUND(ms) AS ms, sql FROM query_log ORDER BY id DESC LIMIT 10",
};
$("sqlPresets").innerHTML = Object.keys(PRESETS).map(k => `<button type="button">${k}</button>`).join("");
$("sqlPresets").addEventListener("click", e => {
  const b = e.target.closest("button");
  if (b) { $("sqlBox").value = PRESETS[b.textContent]; runSql(); }
});
$("sqlRun").onclick = () => runSql();
$("sqlBox").addEventListener("keydown", e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) runSql(); });

async function runSql() {
  $("sqlErr").textContent = "";
  const r = await send("/api/db/sql", { sql: $("sqlBox").value });
  if (r.error) { $("sqlErr").textContent = r.error; $("sqlResultWrap").hidden = true; return; }
  $("sqlResultWrap").hidden = false;
  $("sqlResult").innerHTML = `<tr>${r.columns.map(c => `<th>${esc(c)}</th>`).join("")}</tr>` +
    (r.rows.length ? r.rows.map(row => `<tr>${row.map(v => `<td>${v === null ? '<span class="dim">NULL</span>' : esc(v)}</td>`).join("")}</tr>`).join("")
      : `<tr><td class="dim" colspan="${r.columns.length || 1}">No rows.</td></tr>`) +
    (r.truncated ? `<tr><td class="dim" colspan="${r.columns.length}">Showing the first 200 rows.</td></tr>` : "");
}

async function drawAccess() {
  const d = await get("/api/db/access?limit=40");
  const t = d.totals, total = (t.hit || 0) + (t.miss || 0) + (t.bypass || 0);
  setHTML($("accessTotals"), `<div><b>${total}</b><small>requests logged</small></div><div><b>${t.hit || 0}</b><small>cache hits</small></div><div><b>${(t.miss || 0) + (t.bypass || 0)}</b><small>went to the database</small></div>`);
  setHTML($("accessLog"), `<tr><th>time</th><th>key</th><th>result</th><th>server</th><th>ms</th></tr>` +
    (d.recent.map(r => `<tr><td class="num">${r.ts.slice(11)}</td><td>${esc(r.key)}</td>
      <td><span class="badge-r ${r.result}">${r.result.toUpperCase()}</span>${r.failover ? ' <span class="dim" title="Owner was unreachable">replica</span>' : ""}</td>
      <td>${r.server ? tag(r.server) : '<span class="dim">database</span>'}</td><td class="num">${r.ms.toFixed(1)}</td></tr>`).join("") ||
      `<tr><td colspan="5" class="dim">No requests yet. Read a row or run a flash sale.</td></tr>`));
}
