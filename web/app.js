// SentinelMesh officer console. Public visitors see zone levels only; signed-in officers see evidence,
// approvals, drills and failover. Field filtering happens in the API, not here.
const TIERS = ["normal", "watch", "advisory", "warning", "imminent"];
const TIER_LABEL = { normal: "Normal", watch: "Watch", advisory: "Advisory", warning: "Warning", imminent: "Imminent", blind: "Blind (sensors silent)" };
const COLORS = { normal: "#c9d6dc", watch: "#e7c84b", advisory: "#e8913a", warning: "#d14b3d", imminent: "#7d1220", blind: "#6b5ca5" };

let cfg, geo, map, layers = {}, zones = {}, officer = false, selected = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = Auth.idToken();
  if (token) headers.Authorization = token;
  const r = await fetch(cfg.api_url + path, { ...opts, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

async function boot() {
  cfg = await (await fetch("config.json")).json();
  await Auth.init(cfg);
  const claims = Auth.claims();
  const groups = claims?.["cognito:groups"] || [];
  officer = groups.includes("duty_officer");
  if (claims) {
    $("who").textContent = `${claims.email} · ${officer ? "duty officer" : "viewer"}`;
    $("login").hidden = true;
    $("logout").hidden = false;
  }
  document.querySelectorAll(".officer").forEach((el) => (el.hidden = !officer));
  $("login").onclick = () => Auth.login();
  $("logout").onclick = () => Auth.logout();

  geo = await (await fetch("zones.geojson")).json();
  drawMap();
  fillDrillZones();
  wireActions();
  refresh();
  setInterval(refresh, 4000);
}

function drawMap() {
  map = L.map("map", { zoomControl: true }).setView([6.95, 80.35], 10);
  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: "© OpenStreetMap contributors © CARTO", maxZoom: 14,
  }).addTo(map);
  const features = geo.features.filter((f) => !f.properties.is_test);
  const group = L.geoJSON({ type: "FeatureCollection", features }, {
    style: () => ({ color: "#12324a", weight: 1, fillColor: COLORS.normal, fillOpacity: 0.75 }),
    onEachFeature: (f, layer) => {
      layers[f.properties.zone_id] = layer;
      layer.bindTooltip(`${f.properties.name}`, { sticky: true });
      layer.on("click", () => selectZone(f.properties.zone_id));
    },
  }).addTo(map);
  map.fitBounds(group.getBounds(), { padding: [20, 20] });
}

function fillDrillZones() {
  $("drillzone").innerHTML = geo.features
    .filter((f) => !f.properties.is_test)
    .map((f) => `<option value="${f.properties.zone_id}">${esc(f.properties.zone_id)} · ${esc(f.properties.name)}</option>`)
    .join("");
  $("drillzone").value = "Z07";
}

async function refresh() {
  try {
    const data = officer ? await api("/zones") : await api("/public/zones");
    const list = data.zones || [];
    zones = Object.fromEntries(list.map((z) => [z.zone_id, z]));
    paint();
    $("updated").textContent = `Updated ${new Date().toLocaleTimeString()}. Refreshes every 4 seconds.`;
    if (selected) selectZone(selected, true);
    if (officer) await Promise.all([loadApprovals(), loadAlerts()]);
  } catch (e) {
    $("updated").textContent = `Couldn't load zones: ${e.message}. Retrying.`;
  }
}

function levelOf(z) { return z?.blind ? "blind" : z?.tier || "normal"; }

function paint() {
  const counts = Object.fromEntries([...TIERS, "blind"].map((t) => [t, 0]));
  for (const [id, layer] of Object.entries(layers)) {
    const level = levelOf(zones[id]);
    counts[level] += 1;
    layer.setStyle({ fillColor: COLORS[level], weight: id === selected ? 3 : 1 });
    const el = layer.getElement?.();
    if (el) el.classList.toggle("pulse", level === "imminent");
  }
  const order = [...TIERS, "blind"];
  $("post").innerHTML = order.map((t) => `<div class="tier-${t}" title="${TIER_LABEL[t]}"></div>`).join("");
  $("marks").innerHTML = order.map((t) => `<div><b>${counts[t]}</b><span>${TIER_LABEL[t]}</span></div>`).join("");
}

function selectZone(id, quiet = false) {
  selected = id;
  const z = zones[id] || { zone_id: id, tier: "normal" };
  const f = geo.features.find((x) => x.properties.zone_id === id).properties;
  $("zonecard").hidden = false;
  $("zonetitle").textContent = `${id} · ${f.name}`;
  let html = `<dl class="kv"><dt>Level</dt><dd><span class="chip ${levelOf(z)} tier-${levelOf(z)}">${TIER_LABEL[levelOf(z)]}</span></dd>
    <dt>District</dt><dd>${esc(f.district)}</dd><dt>Hazard</dt><dd>${esc(z.hazard || f.hazard)}</dd>
    <dt>Handled by</dt><dd>cell ${esc(z.eval_cell || z.owner_cell || f.owner_cell)}</dd>`;
  if (z.score !== undefined) html += `<dt>Agreement score</dt><dd>${Number(z.score).toFixed(2)}</dd><dt>Why</dt><dd>${esc((z.reason || "").replace(/_/g, " "))}</dd>`;
  html += "</dl>";
  if (z.contributions && Object.keys(z.contributions).length) {
    html += `<div class="bars">${Object.entries(z.contributions).map(([k, v]) =>
      `<span>${esc(k)}</span><i style="width:${Math.round((v / 0.4) * 100)}%"></i><span>${Number(v).toFixed(2)}</span>`).join("")}</div>`;
  }
  $("zonedetail").innerHTML = html;
  if (!quiet) paint();
}

async function loadApprovals() {
  const { approvals } = await api("/approvals");
  $("approvals").innerHTML = approvals.length
    ? approvals.map((a) => {
        const s = JSON.parse(a.summary || "{}");
        return `<div class="entry"><div class="head"><span class="zone">${esc(a.zone_id)} ${esc(a.hazard)} warning</span>
          <span class="countdown">${a.seconds_left}s</span></div>
          <p>Score ${Number(s.score).toFixed(2)}. Sources agreeing: ${esc((s.agreeing || []).join(", "))}.
          If you don't decide, it is issued when the countdown ends.</p>
          <div class="actions"><button class="primary" data-approve="${esc(a.approval_id)}">Issue warning</button>
          <button data-reject="${esc(a.approval_id)}">Hold back</button></div></div>`;
      }).join("")
    : `<p class="muted">Nothing to approve. Warnings that need a decision appear here with a countdown.</p>`;
}

async function loadAlerts() {
  const { alerts } = await api("/alerts");
  $("alerts").innerHTML = alerts.length
    ? alerts.map((a) => `<div class="entry"><div class="head"><span class="zone">${esc(a.zone_id)} · ${esc(a.zone_name)}</span>
        <span class="chip ${a.tier} tier-${a.tier}">${TIER_LABEL[a.tier]}</span></div>
        <p>${new Date(a.issued_at * 1000).toLocaleTimeString()} · ${esc(a.status)} · ${esc(a.mode)} · sent ${a.sent}, confirmed ${a.delivered}
        ${a.first_receipt_ms ? ` · first phone reached in ${(a.first_receipt_ms / 1000).toFixed(1)} s` : ""}</p>
        <p><a href="${esc(a.cap_key)}" target="_blank" rel="noopener">CAP file</a></p></div>`).join("")
    : `<p class="muted">No alerts in the last two days. Start a drill to see the whole path.</p>`;
}

function wireActions() {
  document.body.addEventListener("click", async (e) => {
    const t = e.target;
    try {
      if (t.dataset.approve || t.dataset.reject) {
        const note = t.dataset.reject ? prompt("Why hold this warning back? This is recorded.") : "approved in console";
        if (t.dataset.reject && !note) return;
        await api(`/approvals/${encodeURIComponent(t.dataset.approve || t.dataset.reject)}`, {
          method: "POST", body: JSON.stringify({ decision: t.dataset.approve ? "approve" : "reject", note }),
        });
        refresh();
      } else if (t.id === "rundrill") {
        const r = await api(`/scenarios/${$("scenario").value}`, { method: "POST", body: JSON.stringify({ zone_id: $("drillzone").value }) });
        $("opsmsg").textContent = `Drill started in ${r.zone_id}. Watch the map and the phone.`;
        selectZone(r.zone_id);
        map.flyTo(layers[r.zone_id].getBounds().getCenter(), 12);
      } else if (t.dataset.act) {
        const r = await api(`/cells/${t.dataset.cell}/${t.dataset.act}`, { method: "POST" });
        $("opsmsg").textContent = `${r.zones_moved} zones moved. New readings now go to the other cell.`;
      }
    } catch (err) {
      $("opsmsg").textContent = err.message;
    }
  });
}

boot();
