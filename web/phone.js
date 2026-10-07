// Simulated handset: opts in with zone + language, polls its inbox (stand-in for SMS) and the zone's
// broadcast object (stand-in for cell broadcast), shows the alert full-screen and confirms receipt.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const store = { get: (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
const HEAD = { imminent: { en: "Emergency alert", si: "හදිසි අනතුරු ඇඟවීම", ta: "அவசர எச்சரிக்கை" },
               warning: { en: "Warning", si: "අනතුරු ඇඟවීම", ta: "எச்சரிக்கை" },
               advisory: { en: "Advisory", si: "දැනුම්දීම", ta: "அறிவிப்பு" } };
let cfg, geo, me = store.get("sm-phone"), seen = new Set(store.get("sm-seen") || []);

function geohash(lat, lon, len = 6) {
  const base32 = "0123456789bcdefghjkmnpqrstuvwxyz";
  let latR = [-90, 90], lonR = [-180, 180], hash = "", bit = 0, ch = 0, even = true;
  while (hash.length < len) {
    const r = even ? lonR : latR, v = even ? lon : lat, mid = (r[0] + r[1]) / 2;
    if (v >= mid) { ch |= 1 << (4 - bit); r[0] = mid; } else { r[1] = mid; }
    even = !even;
    if (++bit === 5) { hash += base32[ch]; bit = 0; ch = 0; }
  }
  return hash;
}

async function call(path, opts = {}) {
  const headers = { "Content-Type": "application/json" };
  if (me) headers["X-Device-Token"] = me.token;
  const r = await fetch(cfg.api_url + path, { ...opts, headers });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error || `Request failed (${r.status})`);
  return body;
}

async function boot() {
  setInterval(() => ($("clock").textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })), 1000);
  cfg = await (await fetch("config.json")).json();
  geo = await (await fetch("zones.geojson")).json();
  $("zone").innerHTML = geo.features.filter((f) => !f.properties.is_test)
    .map((f) => `<option value="${esc(f.properties.zone_id)}">${esc(f.properties.name)} (${esc(f.properties.zone_id)})</option>`).join("");
  $("zone").value = new URLSearchParams(location.search).get("zone") || "Z07";
  $("join").onclick = join;
  $("reset").onclick = () => { localStorage.removeItem("sm-phone"); location.reload(); };
  $("dismiss").onclick = () => ($("alert").hidden = true);
  if (me) showHome();
}

async function join() {
  const zone = $("zone").value;
  const f = geo.features.find((x) => x.properties.zone_id === zone);
  const [lon, lat] = f.geometry.coordinates[0][0];
  const token = crypto.getRandomValues(new Uint32Array(4)).join("-");
  try {
    $("join").disabled = true;
    me = { token, zone, lang: $("lang").value, name: f.properties.name };
    const r = await call("/phone/subscribe", { method: "POST", body: JSON.stringify({ zone_id: zone, lang: me.lang, geohash6: geohash(lat, lon), device_token: token }) });
    me.sid = r.subscriber_id;
    store.set("sm-phone", me);
    showHome();
  } catch (e) {
    me = null;
    $("setupmsg").textContent = `Couldn't turn on warnings: ${e.message}`;
  } finally {
    $("join").disabled = false;
  }
}

function showHome() {
  $("setup").hidden = true;
  $("home").hidden = false;
  $("homearea").textContent = me.name;
  poll();
  setInterval(poll, 3000);
}

async function poll() {
  try {
    const { messages } = await call(`/phone/inbox?subscriber_id=${encodeURIComponent(me.sid)}`);
    for (const m of messages.slice().reverse()) {
      if (seen.has(m.sk)) continue;
      seen.add(m.sk);
      store.set("sm-seen", [...seen]);
      show(m);
      call("/phone/ack", { method: "POST", body: JSON.stringify({ sk: m.sk }) })
        .then((r) => { if (r.ttfa_ms) $("alertmeta").textContent += ` Reached this phone ${(r.ttfa_ms / 1000).toFixed(1)} s after the first reading.`; })
        .catch(() => {});
    }
    $("history").innerHTML = messages.map((m) => `<div class="msg">${esc(m.text)}<small>${new Date(Number(m.sk.split("#")[0])).toLocaleString()} · ${m.channel === "sms" ? "SMS" : "Alert"}${m.status !== "Actual" ? ` · ${m.status}` : ""}</small></div>`).join("")
      || `<p class="fine">No alerts yet.</p>`;
  } catch (e) {
    $("homestatus").textContent = `Can't reach the warning service right now (${e.message}). Retrying.`;
  }
}

function show(m) {
  const box = $("alert");
  box.className = `alert ${m.tier}`;
  $("alerthead").textContent = (HEAD[m.tier] || HEAD.warning)[me.lang] + (m.status !== "Actual" ? ` (${m.status})` : "");
  $("alerttext").textContent = m.text;
  $("alertmeta").textContent = `${me.name}. ${new Date().toLocaleTimeString()}.`;
  box.hidden = false;
  if (navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 800]);
}

boot();
