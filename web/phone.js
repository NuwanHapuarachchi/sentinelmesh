// Simulated handset. Two channels, as on a real phone:
// - Cell broadcast: polls the zone's broadcast object (cb/{zone}.json via CloudFront). Needs no sign-up and
//   sends no receipt, exactly like a real cell broadcast.
// - SMS: after opting in with zone + language, polls its inbox (stand-in for SMS) and confirms receipt.
// An alert that arrives on both channels pops up once.
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const store = { get: (k) => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
                set: (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode */ } } };
const HEAD = { imminent: { en: "Emergency alert", si: "හදිසි අනතුරු ඇඟවීම", ta: "அவசர எச்சரிக்கை" },
               warning: { en: "Warning", si: "අනතුරු ඇඟවීම", ta: "எச்சரிக்கை" },
               advisory: { en: "Advisory", si: "දැනුම්දීම", ta: "அறிவிப்பு" },
               clear: { en: "All clear", si: "අවදානම අවසන්", ta: "அபாயம் நீங்கியது" } };
const BROADCAST_MAX_AGE_S = 6 * 3600; // CAP alerts expire after 6 hours; older broadcasts are not shown
let cfg, geo, me = store.get("sm-phone"), seen = new Set(store.get("sm-seen") || []);
let shown = new Set(store.get("sm-shown") || []), broadcasts = store.get("sm-cb") || [], inbox = [];

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
  if (me?.token) headers["X-Device-Token"] = me.token;
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
  $("listen").onclick = listen;
  $("reset").onclick = () => {
    for (const k of ["sm-phone", "sm-seen", "sm-shown", "sm-cb"]) localStorage.removeItem(k);
    location.reload();
  };
  $("dismiss").onclick = () => ($("alert").hidden = true);
  $("stop").onclick = stop;
  if (me) showHome();
}

function chosenArea() {
  const zone = $("zone").value;
  const f = geo.features.find((x) => x.properties.zone_id === zone);
  return { zone, f, lang: $("lang").value, name: f.properties.name };
}

// With a number: first click sends a code, second click checks it and signs up with the number.
let pendingNumber = null;

async function join() {
  const { zone, f, lang, name } = chosenArea();
  const [lon, lat] = f.geometry.coordinates[0][0];
  const number = $("msisdn").value.trim();
  try {
    $("join").disabled = true;
    $("setupmsg").textContent = "";
    if (number && pendingNumber !== number) {
      const r = await call("/phone/verify/start", { method: "POST", body: JSON.stringify({ msisdn: number }) });
      pendingNumber = number;
      $("codebox").hidden = false;
      $("codehint").textContent = r.simulator_code
        ? `No SMS provider in this demo stack. Your code is ${r.simulator_code}.`
        : "We sent a 6-digit code by SMS. It expires in 10 minutes.";
      $("join").textContent = "Confirm code and turn on warnings";
      return;
    }
    const body = { zone_id: zone, lang, geohash6: geohash(lat, lon) };
    if (number) {
      const v = await call("/phone/verify/finish", { method: "POST", body: JSON.stringify({ msisdn: number, code: $("code").value.trim() }) });
      Object.assign(body, { msisdn: number, verify_token: v.verify_token });
    }
    const token = crypto.getRandomValues(new Uint32Array(4)).join("-");
    me = { token, zone, lang, name };
    const r = await call("/phone/subscribe", { method: "POST", body: JSON.stringify({ ...body, device_token: token }) });
    Object.assign(me, { sid: r.subscriber_id, sms: r.sms });
    store.set("sm-phone", me);
    showHome();
  } catch (e) {
    me = null;
    $("setupmsg").textContent = `Couldn't turn on warnings: ${e.message}`;
  } finally {
    $("join").disabled = false;
  }
}

async function stop() {
  // Withdraw consent: the server erases this phone's record (and number), then the phone forgets it too.
  try {
    await call(`/phone/subscribe?subscriber_id=${encodeURIComponent(me.sid)}`, { method: "DELETE" });
  } catch (e) {
    $("homestatus").textContent = `Couldn't stop warnings: ${e.message}`;
    return;
  }
  for (const k of ["sm-phone", "sm-seen", "sm-shown", "sm-cb"]) localStorage.removeItem(k);
  location.reload();
}

function listen() {
  // Broadcast-only: nothing is sent anywhere; the phone just listens to its area's towers.
  const { zone, lang, name } = chosenArea();
  me = { zone, lang, name };
  store.set("sm-phone", me);
  showHome();
}

function showHome() {
  $("setup").hidden = true;
  $("home").hidden = false;
  $("homearea").textContent = me.name;
  $("homestatus").textContent = me.sid
    ? `Warnings are on: emergency broadcasts${me.sms ? ", SMS to your number" : ""} and alerts in this app. This phone checks every few seconds.`
    : "Listening for emergency broadcasts in this area. No sign-up, nothing sent from this phone.";
  $("stop").hidden = !me.sid;
  render();
  pollBroadcast();
  setInterval(pollBroadcast, 2000);
  if (me.sid) {
    poll();
    setInterval(poll, 3000);
  }
}

async function pollBroadcast() {
  try {
    const r = await fetch(`cb/${encodeURIComponent(me.zone)}.json`, { cache: "no-store" });
    if (!r.ok) return; // nothing has been broadcast in this area yet
    const b = await r.json();
    const key = `cb#${b.alert_id}`;
    if (seen.has(key)) return;
    seen.add(key);
    store.set("sm-seen", [...seen]);
    if (Date.now() / 1000 - Number(b.broadcast_at) > BROADCAST_MAX_AGE_S) return;
    const text = (b.texts || {})[me.lang] || (b.texts || {}).en || "";
    broadcasts = [{ alert_id: b.alert_id, tier: b.tier, status: b.status, text, at: Number(b.broadcast_at) * 1000 }, ...broadcasts].slice(0, 10);
    store.set("sm-cb", broadcasts);
    show({ alert_id: b.alert_id, tier: b.tier, status: b.status, text }, "Cell broadcast");
    render();
  } catch {
    // offline or a half-written object: try again on the next tick
  }
}

async function poll() {
  try {
    const { messages } = await call(`/phone/inbox?subscriber_id=${encodeURIComponent(me.sid)}`);
    inbox = messages;
    for (const m of messages.slice().reverse()) {
      if (seen.has(m.sk)) continue;
      seen.add(m.sk);
      store.set("sm-seen", [...seen]);
      show(m, "SMS");
      call("/phone/ack", { method: "POST", body: JSON.stringify({ sk: m.sk }) })
        .then((r) => { if (r.ttfa_ms) $("alertmeta").textContent += ` Reached this phone ${(r.ttfa_ms / 1000).toFixed(1)} s after the first reading.`; })
        .catch(() => {});
    }
    render();
  } catch (e) {
    $("homestatus").textContent = `Can't reach the warning service right now (${e.message}). Retrying.`;
  }
}

function render() {
  const items = [
    ...inbox.map((m) => ({ text: m.text, at: Number(m.sk.split("#")[0]), label: m.channel === "sms" ? "SMS" : "Alert", status: m.status, cls: "" })),
    ...broadcasts.map((b) => ({ text: b.text, at: b.at, label: "Cell broadcast", status: b.status, cls: " cb" })),
  ].sort((a, b) => b.at - a.at);
  $("history").innerHTML = items.map((m) => `<div class="msg${m.cls}">${esc(m.text)}<small>${new Date(m.at).toLocaleString()} · ${m.label}${m.status !== "Actual" ? ` · ${esc(m.status)}` : ""}</small></div>`).join("")
    || `<p class="fine">No alerts yet.</p>`;
}

function show(m, channel) {
  // One full-screen alert per alert id, whichever channel delivers it first.
  if (m.alert_id && shown.has(m.alert_id)) return;
  if (m.alert_id) {
    shown.add(m.alert_id);
    store.set("sm-shown", [...shown]);
  }
  const box = $("alert");
  box.className = `alert ${m.tier}`;
  $("alerthead").textContent = (HEAD[m.tier] || HEAD.warning)[me.lang] + (m.status !== "Actual" ? ` (${m.status})` : "");
  $("alerttext").textContent = m.text;
  $("alertmeta").textContent = `${channel}. ${me.name}. ${new Date().toLocaleTimeString()}.`;
  box.hidden = false;
  if (navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 800]);
}

boot();
