const $ = id => document.getElementById(id);
const COLORS = { A: "#4c6ef5", B: "#12b886", C: "#f76707", D: "#e64980", E: "#ae3ec9", F: "#15aabf", G: "#fab005", H: "#868e96" };
const col = n => COLORS[n] || "#868e96";
const CAT = { Footwear: "#4c6ef5", Audio: "#12b886", Wearables: "#f76707", Bags: "#c2410c", Photo: "#0b7285", Apparel: "#d6336c", Computing: "#5f3dc4", Outdoors: "#2b8a3e", Home: "#495057" };
const SHOWN = 36;

let S = null;
let locateResult = null;
let lastLoad = null;
const tourDone = new Set();
let tourBusy = null;

const setHTML = (el, html) => { if (el.dataset.h !== html) { el.innerHTML = html; el.dataset.h = html; } };
const get = url => fetch(url).then(r => r.json());
async function post(url, body = {}) {
  $("err").textContent = "";
  const j = await (await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
  if (j.error) $("err").textContent = j.error;
  await poll(true);
  return j;
}

/* ---------------- routing ---------------- */
const VIEWS = ["learn", "shop", "lab", "database", "control", "split"];
function route() {
  const v = VIEWS.includes(location.hash.slice(1)) ? location.hash.slice(1) : "learn";
  document.body.dataset.view = v;
  window.scrollTo(0, 0);
  poll(true);
}
window.addEventListener("hashchange", route);

/* ---------------- ring ---------------- */
const C = 220, TICK_IN = 172, TICK_OUT = 188, DOTS = 146, BAND = 164, WALK_R = 180;
const pt = (deg, r) => { const a = deg * Math.PI / 180; return [C + r * Math.sin(a), C - r * Math.cos(a)]; };
const f = n => n.toFixed(1);
const esc = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function arcPath(a1, a2, r) {
  const span = (((a2 - a1) % 360) + 360) % 360;
  const [x1, y1] = pt(a1, r), [x2, y2] = pt(a2, r);
  return `M${f(x1)} ${f(y1)} A${r} ${r} 0 ${span > 180 ? 1 : 0} 1 ${f(x2)} ${f(y2)}`;
}

/* The coloured band is the heart of consistent hashing: each stretch of the ring
   belongs to the server whose virtual node ends it (keys walk clockwise to it). */
function bandSVG(ticks) {
  if (!ticks.length) return "";
  if (ticks.length === 1) return `<circle cx="${C}" cy="${C}" r="${BAND}" fill="none" stroke="${col(ticks[0].node)}" stroke-width="12"/>`;
  let o = "";
  ticks.forEach((t, i) => {
    const prev = ticks[(i + ticks.length - 1) % ticks.length].angle;
    o += `<path d="${arcPath(prev, t.angle, BAND)}" fill="none" stroke="${col(t.node)}" stroke-width="12" opacity=".9"/>`;
  });
  return o;
}

/* o.ticks / o.owners: show a hypothetical ring. o.moved: Map id -> {from,to} to ring with a halo.
   o.loc + o.stage: draw a key lookup (1 = key marker, 2 = owner arc, 3 = replica arc). */
function ringSVG(s, o = {}) {
  const ticks = o.ticks || s.ticks;
  let out = `<circle cx="${C}" cy="${C}" r="${TICK_IN}" fill="none" stroke="#334155" stroke-width="1"/>` + bandSVG(ticks);
  for (const t of ticks) {
    const [x1, y1] = pt(t.angle, TICK_IN), [x2, y2] = pt(t.angle, TICK_OUT);
    out += `<line x1="${f(x1)}" y1="${f(y1)}" x2="${f(x2)}" y2="${f(y2)}" stroke="${col(t.node)}" stroke-width="1.6" opacity=".75"/>`;
  }
  const byId = {};
  for (const k of s.keys) {
    byId[k.id] = k;
    const owner = o.owners ? o.owners[k.id] : k.owner;
    const [x, y] = pt(k.angle, DOTS), c = owner ? col(owner) : "#64748b";
    const filled = o.owners ? true : k.cached;
    const mv = o.moved && o.moved.get(k.id);
    let tip = `${k.name} (product:${k.id}): owner ${owner || "none"}`;
    if (!o.owners) tip += (k.replicas.length ? `, copy on ${k.replicas.join(", ")}` : "") + (k.cached ? "" : ", not cached yet");
    if (mv) tip += mv.from === "?" ? " | moved in the last change" : ` | moves ${mv.from} to ${mv.to}`;
    if (mv) out += `<circle cx="${f(x)}" cy="${f(y)}" r="9" fill="none" stroke="#fff" stroke-width="2"/>`;
    out += `<circle cx="${f(x)}" cy="${f(y)}" r="3.8" fill="${filled ? c : "none"}" stroke="${c}" stroke-width="1.5"><title>${esc(tip)}</title></circle>`;
  }
  if (!o.noPulse) for (const r of s.recent) {
    const k = byId[r.id];
    if (!k) continue;
    const [x, y] = pt(k.angle, DOTS);
    out += `<circle class="pulse" cx="${f(x)}" cy="${f(y)}" r="6" style="animation-delay:-${r.age_ms}ms"/>`;
  }
  const loc = o.loc, stage = o.stage ?? 3;
  if (loc && loc.walk && loc.walk.length && stage >= 1) {
    const [kx, ky] = pt(loc.angle, DOTS - 22);
    out += `<line x1="${f(kx)}" y1="${f(ky)}" x2="${f(pt(loc.angle, DOTS)[0])}" y2="${f(pt(loc.angle, DOTS)[1])}" stroke="#fff" stroke-width="1.5"/>
            <circle cx="${f(kx)}" cy="${f(ky)}" r="7" fill="#fff"/>
            <text x="${f(pt(loc.angle, DOTS - 38)[0])}" y="${f(pt(loc.angle, DOTS - 38)[1] + 4)}" text-anchor="middle" font-size="12" fill="#fff">key</text>`;
    const first = loc.walk[0], newHops = loc.walk.filter(h => h.new);
    if (stage >= 2) {
      const [ox, oy] = pt(first.angle, WALK_R);
      out += `<path d="${arcPath(loc.angle, first.angle, WALK_R)}" fill="none" stroke="#fff" stroke-width="3.5" stroke-linecap="round"/>
              <circle cx="${f(ox)}" cy="${f(oy)}" r="7" fill="${col(first.node)}" stroke="#fff" stroke-width="2.5"/>`;
      const [lx, ly] = pt(first.angle, WALK_R + 24);
      out += `<text x="${f(lx)}" y="${f(ly + 4)}" text-anchor="middle" font-size="12" font-weight="600" fill="#fff">${first.label}</text>`;
    }
    if (stage >= 3 && newHops.length > 1) {
      const rep = newHops[1], [rx, ry] = pt(rep.angle, WALK_R);
      out += `<path d="${arcPath(first.angle, rep.angle, WALK_R)}" fill="none" stroke="#fff" stroke-width="2.5" stroke-dasharray="5 5" stroke-linecap="round"/>
              <circle cx="${f(rx)}" cy="${f(ry)}" r="6" fill="${col(rep.node)}" stroke="#fff" stroke-width="2"/>`;
      const [lx, ly] = pt(rep.angle, WALK_R + 24);
      out += `<text x="${f(lx)}" y="${f(ly + 4)}" text-anchor="middle" font-size="12" fill="#cbd5e1">${rep.label}</text>`;
    }
  }
  const n = o.center ?? s.nodes.filter(x => x.in_ring).length;
  out += `<text x="${C}" y="${C - 2}" text-anchor="middle" font-size="56" font-weight="700" font-family="var(--display)" fill="#fff">${n}</text>
        <text x="${C}" y="${C + 24}" text-anchor="middle" font-size="15" fill="#cbd5e1">${n === 1 ? "server" : "servers"} in the ring</text>
        <text x="${C}" y="${C + 44}" text-anchor="middle" font-size="12.5" fill="#94a3b8">${s.vnodes} virtual nodes each, ${s.rf} copies per key</text>`;
  return out;
}

/* Grid of one square per product; coloured squares are the keys that move. */
function gridHTML(ids, total, color) {
  const set = new Set(ids);
  let h = "";
  for (let i = 0; i < total; i++) h += `<i class="${set.has(i) ? "mv" : ""}" title="product:${i}${set.has(i) ? " moves" : " stays"}"></i>`;
  return `<div class="gridmap" style="--mv:${color}">${h}</div>`;
}

/* ---------------- header chips ---------------- */
function drawChips(s) {
  setHTML($("chips"), s.nodes.map(n => {
    const label = n.status === "up" ? `Server ${n.name}` : `Server ${n.name} ${n.status}`;
    return `<span class="chip ${n.status}" style="--c:${col(n.name)}"><i></i>${label}</span>`;
  }).join(""));
}

/* ---------------- learn: locate ---------------- */
async function locate(e) {
  if (e) e.preventDefault();
  const key = $("locateInput").value.trim();
  if (!key) { $("locateOut").textContent = "Enter a key first, for example product:42."; return; }
  locateResult = await get("/api/locate?key=" + encodeURIComponent(key));
  const l = locateResult, tag = n => `<span class="nd" style="--c:${col(n)}">${n}</span>`;
  $("locateOut").innerHTML = l.nodes.length
    ? `<b>${l.key.replace(/</g, "&lt;")}</b> hashes to ${Math.round(l.angle)}°. Owner: ${tag(l.nodes[0])}` +
      (l.nodes[1] ? `, spare copy on ${tag(l.nodes[1])}.` : ".") + ` The white arc shows the walk clockwise to the owner.`
    : "No servers are in the ring.";
  if (S) $("ringHero").innerHTML = ringSVG(S, { loc: locateResult, stage: 3 });
}
$("locateForm").addEventListener("submit", locate);

/* ---------------- shop ---------------- */
let shopper = null;
async function loadCatalog() {
  const products = (await get("/api/catalog")).slice(0, SHOWN);
  $("grid").innerHTML = products.map(p => `
    <article class="prod" data-id="${p.id}">
      <div class="img" style="background:${CAT[p.category] || "#495057"}">${p.name.split(" ").pop()[0].toUpperCase()}</div>
      <div class="body">
        <h3>${p.name}</h3><span class="cat">${p.category}</span>
        <span class="price">$${p.price.toFixed(2)}</span>
        <div class="badge idle">Not opened yet</div>
        <button class="btn small" data-open="${p.id}">Open product</button>
      </div>
    </article>`).join("");
}
$("grid").addEventListener("click", e => { const b = e.target.closest("[data-open]"); if (b) openProduct(+b.dataset.open); });

async function openProduct(id) {
  const card = document.querySelector(`.prod[data-id="${id}"]`);
  if (!card) return;
  const badge = card.querySelector(".badge");
  badge.className = "badge loading"; badge.textContent = "Loading…";
  const r = await get(`/api/product/${id}`);
  lastLoad = r;
  if (r.source === "cache") { badge.className = "badge cache"; badge.textContent = `From cache, node ${r.node}, ${r.ms} ms`; }
  else { badge.className = "badge database"; badge.textContent = `From database, ${r.ms} ms`; }
  card.classList.remove("flash"); void card.offsetWidth; card.classList.add("flash");
  drawPath();
}

function drawPath() {
  const r = lastLoad;
  if (!r) return;
  const hop = (cls, mark, text, c) => `<div class="hop ${cls}" ${c ? `style="--c:${c}"` : ""}><i>${mark}</i><span>${text}</span></div>`;
  let hops = hop("on", "", "Your browser") + hop("on", "", "Coordinator looks the key up on the ring");
  for (const n of r.skipped) hops += hop("miss", "!", `Server ${n} did not answer`);
  if (r.source === "cache") hops += hop("on", r.node, `Server ${r.node} answered from memory`, col(r.node));
  else {
    hops += hop("off", "", S && !S.cache_enabled ? "Cache is switched off" : "Cache had no copy yet");
    hops += hop("on", "DB", "Database query, then copied into the cache", "#b54708");
  }
  const note = r.failover ? `<p class="path-note">The key's owner was unreachable, so its replica answered. The shopper noticed nothing.</p>`
    : r.source === "database" ? `<p class="path-note">Open the same product again to see the cache answer.</p>` : "";
  $("path").className = "";
  $("path").innerHTML = `<div class="path-verdict ${r.source}">${r.source === "cache" ? "Served from cache" : "Served from database"}</div>
    <div class="path-time num">${r.ms} <small>ms</small></div>
    <div>${r.product.name}</div><div class="hops">${hops}</div>${note}`;
}

$("shopper").onclick = e => {
  if (shopper) { clearInterval(shopper); shopper = null; e.target.textContent = "Start auto-shopper"; e.target.classList.add("primary"); }
  else {
    shopper = setInterval(() => openProduct(Math.floor(SHOWN * Math.random() ** 1.6)), 450);
    e.target.textContent = "Stop auto-shopper"; e.target.classList.remove("primary");
  }
};

/* ---------------- control room ---------------- */
function drawKpis(m) {
  const k = (label, value, note, alert) => `<div class="kpi ${alert ? "alert" : ""}"><small>${label}</small><b>${value}</b><span>${note}</span></div>`;
  $("kpis").innerHTML =
    k("Cache hit ratio", m.hit_ratio + "%", "of reads answered without the database") +
    k("Average response", m.avg_ms + " ms", `95% finish within ${m.p95_ms} ms`) +
    k("Database queries", m.db_queries, "each one costs about 180 ms") +
    k("Failover reads", m.failovers, "served by a replica", m.failovers > 0);
}

function drawNodes(s) {
  const max = Math.max(1, ...s.nodes.map(n => n.owns));
  setHTML($("nodes"), s.nodes.map(n => {
    const st = n.stats;
    return `<div class="node ${n.status === "down" ? "dead" : ""}" style="--c:${col(n.name)}">
      <h3>Server ${n.name} <span class="st ${n.status}">${n.status === "up" ? "healthy" : n.status === "suspect" ? "not answering" : "down"}</span></h3>
      <div class="meter" title="Share of the 200 products this server owns"><i style="width:${100 * n.owns / max}%"></i></div>
      <div class="row"><span>Products owned</span><b>${n.owns}</b></div>
      <div class="row"><span>Stored (with copies)</span><b>${st ? st.size + " / " + st.capacity : "n/a"}</b></div>
      <div class="row"><span>Hits and misses</span><b>${st ? st.hits + " / " + st.misses : "n/a"}</b></div>
      <div class="row"><span>Port</span><b>${n.port}</b></div></div>`;
  }).join(""));
  setHTML($("legend"), s.nodes.map(n => `<span class="${n.status === "down" ? "down" : ""}" style="--c:${col(n.name)}"><i></i>Server ${n.name}</span>`).join(""));
  const sel = $("victim"), prev = sel.value;
  const live = s.nodes.filter(n => n.local && n.status !== "down").map(n => n.name);
  sel.innerHTML = live.map(n => `<option value="${n}">Server ${n}</option>`).join("");
  if (live.includes(prev)) sel.value = prev;
}

function drawCompare(c) {
  if (!c) return;
  const row = (label, v, color) => `<div class="cmp"><span>${label}</span><div class="track"><i style="width:${100 * v / c.total}%;background:${color}"></i></div><b>${v} of ${c.total}</b></div>`;
  $("compare").className = "";
  setHTML($("compare"), `<div class="cmp-title">${c.event}</div>` +
    row("Consistent hashing", c.ring_moved, "var(--ok)") + row("Plain hash % N", c.modulo_moved, "var(--bad)") +
    `<div class="maps"><div><small>Consistent hashing: coloured squares are keys that moved</small>${gridHTML(c.ring_ids, c.total, "#079455")}</div>
      <div><small>Plain hash % N would have moved</small>${gridHTML(c.modulo_ids, c.total, "#d92d20")}</div></div>
    <p class="cmp-note">Each square is one of the ${c.total} products. ${c.copied} entries were copied between servers, including rebuilt replicas.</p>`);
}

function drawLog(events) {
  setHTML($("log"), events.map(e => `<div class="${e.kind}"><time>${e.t}</time><span>${e.msg}</span></div>`).join(""));
}

/* guided tour */
const waitIdle = async () => { do { await new Promise(r => setTimeout(r, 400)); await poll(true); } while (S.traffic.running); };
async function runSale(n) { await post("/api/traffic", { requests: n }); await new Promise(r => setTimeout(r, 300)); await poll(true); await waitIdle(); }
const busiest = () => S.nodes.filter(n => n.local && n.status === "up" && n.in_ring).sort((a, b) => b.owns - a.owns)[0];
const TOUR = [
  { id: "fill", title: "Fill the cache", watch: "Hollow dots turn solid and the hit ratio climbs.", btn: "Run flash sale",
    run: () => runSale(1000) },
  { id: "nocache", title: "See what the cache saves", watch: "With the cache off, every read waits on the database and response time jumps.", btn: "Run without cache",
    run: async () => { await post("/api/settings", { cache_enabled: false }); await runSale(300); await post("/api/settings", { cache_enabled: true }); } },
  { id: "add", title: "Add a server", watch: "A few dots change colour. The comparison below shows how little moved.", btn: "Add server",
    run: () => post("/api/nodes", { action: "add" }) },
  { id: "kill", title: "Kill a server", watch: "The shop keeps working. Failover reads count up until the server is declared down.", btn: "Kill busiest server",
    run: async () => { const n = busiest(); if (n) await post("/api/nodes", { action: "kill", name: n.name }); } },
  { id: "revive", title: "Bring it back", watch: "It restarts empty, rejoins the ring, and keys flow back to it.", btn: "Revive server",
    run: () => post("/api/nodes", { action: "add" }) },
];

function drawTour() {
  const lastDone = Math.max(-1, ...TOUR.map((t, i) => tourDone.has(t.id) ? i : -1));
  const next = TOUR.find((t, i) => i > lastDone && !tourDone.has(t.id)) || TOUR.find(t => !tourDone.has(t.id));
  setHTML($("tour"), TOUR.map(t => {
    const cls = tourDone.has(t.id) ? "done" : t === next ? "next" : "";
    const busy = tourBusy === t.id;
    return `<li class="${cls}"><span class="mark"></span><h3>${t.title}</h3>
      <button class="btn small go ${t === next ? "primary" : ""} ${busy ? "busy" : ""}" data-step="${t.id}">${busy ? "Working…" : tourDone.has(t.id) ? "Run again" : t.btn}</button>
      <p>${t.watch}</p></li>`;
  }).join(""));
}
$("tour").addEventListener("click", async e => {
  const b = e.target.closest("[data-step]");
  if (!b || tourBusy) return;
  const step = TOUR.find(t => t.id === b.dataset.step);
  tourBusy = step.id; drawTour();
  try { await step.run(); tourDone.add(step.id); } finally { tourBusy = null; drawTour(); }
});
$("resetDemo").onclick = async () => {
  for (const n of S.nodes.filter(n => n.local && n.status === "down")) await post("/api/nodes", { action: "add", name: n.name });
  await post("/api/settings", { cache_enabled: true });
  await post("/api/reset");
  tourDone.clear(); drawTour();
};

/* manual controls */
$("sale").onclick = () => post("/api/traffic", { requests: +$("reqs").value });
$("add").onclick = () => post("/api/nodes", { action: "add" });
$("kill").onclick = () => post("/api/nodes", { action: "kill", name: $("victim").value });
$("reset").onclick = () => post("/api/reset");
$("cacheOn").onchange = e => post("/api/settings", { cache_enabled: e.target.checked });
let dragging = false;
$("vnodes").oninput = e => { dragging = true; $("vnodesOut").textContent = e.target.value; };
$("vnodes").onchange = e => { dragging = false; post("/api/settings", { vnodes: +e.target.value }); };

/* ---------------- polling ---------------- */
async function poll(force) {
  if (document.hidden && !force) return;
  try { S = await get("/api/state"); } catch (e) { return; }
  const v = document.body.dataset.view;
  drawChips(S);
  if (v === "learn") $("ringHero").innerHTML = ringSVG(S, { loc: locateResult, stage: 3 });
  if (v === "control" || v === "split") {
    const fresh = S.last_change && S.last_change.age_s < 20;
    const moved = fresh ? new Map(S.last_change.ring_ids.map(id => [id, { from: "?", to: "?" }])) : null;
    $("ringControl").innerHTML = ringSVG(S, { moved });
    drawKpis(S.metrics); drawNodes(S); drawCompare(S.last_change); drawLog(S.events);
    $("cacheOn").checked = S.cache_enabled;
    if (!dragging) { $("vnodes").value = S.vnodes; $("vnodesOut").textContent = S.vnodes; }
    if (!tourBusy) drawTour();
  }
  if (typeof labTick === "function") labTick(S, v);
}
(async function loop() { await poll(); setTimeout(loop, 700); })();

loadCatalog();
route();
locate();
