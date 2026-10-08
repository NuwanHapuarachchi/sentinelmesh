// Runs web/app.js (the agency console) in a stub browser with stub Leaflet and Cognito, once per role.
//   node --test tests/web/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../web/app.js", import.meta.url), "utf8");
const feature = (zone_id, district) => ({
  geometry: { type: "Polygon", coordinates: [[[80.4, 6.7]]] },
  properties: { zone_id, name: `Zone ${zone_id}`, district, hazard: "flood", owner_cell: "a", is_test: false },
});
const GEO = { features: [feature("Z07", "Ratnapura"), feature("Z35", "Kegalle")] };

function element() {
  return { textContent: "", innerHTML: "", hidden: false, value: "", onclick: null, dataset: {} };
}

function browser({ groups = [], district, api = {} }) {
  const els = new Proxy({}, { get: (t, id) => (t[id] ??= element()) });
  const officerSections = [element(), element()];
  const layers = {};
  const calls = [];
  const json = (body) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });
  const fetch = (url) => {
    calls.push(url);
    if (url === "config.json") return json({ api_url: "https://api.test/v1" });
    if (url === "zones.geojson") return json(GEO);
    if (url === "public/zones.json") return json({ zones: [{ zone_id: "Z07", tier: "warning", blind: false }] });
    const path = url.replace("https://api.test/v1", "");
    if (path in api) return json(api[path]);
    throw new Error(`unexpected fetch ${url}`);
  };
  const layer = (id) => (layers[id] = { style: {}, bindTooltip() {}, on() {}, setStyle(s) { Object.assign(this.style, s); },
    getElement: () => null, getBounds: () => ({ getCenter: () => [0, 0] }) });
  const L = {
    map: () => ({ setView() { return this; }, fitBounds() {}, flyTo() {} }),
    tileLayer: () => ({ addTo() {} }),
    geoJSON: (data, opts) => {
      for (const f of data.features) opts.onEachFeature(f, layer(f.properties.zone_id));
      return { addTo() { return this; }, getBounds: () => ({}) };
    },
  };
  const claims = groups.length ? { email: "u@test", "cognito:groups": groups, ...(district ? { "custom:district": district } : {}) } : null;
  const Auth = { init: async () => {}, claims: () => claims, idToken: () => (claims ? "token" : null), login() {}, logout() {} };
  const context = vm.createContext({
    document: { getElementById: (id) => els[id], querySelectorAll: () => officerSections, body: { addEventListener() {} } },
    fetch, L, Auth, setInterval: () => 0, prompt: () => null, Date, JSON, Number, String, Object, Math, Promise, console,
  });
  vm.runInContext(SOURCE, context);
  return { els, layers, calls, officerSections };
}

async function settled(b) {
  for (let i = 0; i < 20; i++) await new Promise((r) => setTimeout(r, 0));
  return b;
}

const ZONES = { zones: [{ zone_id: "Z35", district: "Kegalle", tier: "warning", blind: false, hazard: "flood" }] };
const ALERTS = { alerts: [{ alert_id: "A1", zone_id: "Z35", zone_name: "Kegalle 02", tier: "warning", status: "Actual",
  issued_at: 1791400000, cap_key: "cap/x/A1.xml", sent: 10, delivered: 9 }] };

test("emergency services see their district and the rest faded, without officer tools", async () => {
  const b = await settled(browser({ groups: ["ems"], district: "Kegalle", api: { "/zones": ZONES, "/alerts": ALERTS } }));
  assert.match(b.els.who.textContent, /emergency services · Kegalle$/);
  assert.equal(b.layers.Z07.style.fillOpacity, 0.12);
  assert.equal(b.layers.Z35.style.fillOpacity, 0.75);
  assert.ok(b.officerSections.every((s) => s.hidden), "drills, failover and approvals stay hidden");
  assert.ok(!b.calls.some((u) => u.endsWith("/approvals")));
  assert.match(b.els.alerts.innerHTML, /sent 10, confirmed 9/);
});

test("meteorology sees alerts without people counts", async () => {
  const met = { alerts: [{ ...ALERTS.alerts[0], sent: undefined, delivered: undefined, mode: "auto" }] };
  const b = await settled(browser({ groups: ["met"], api: { "/zones": ZONES, "/alerts": met } }));
  assert.doesNotMatch(b.els.alerts.innerHTML, /sent|confirmed/);
  assert.match(b.els.alerts.innerHTML, /Actual · auto/);
});

test("NGOs see the public map and counts only", async () => {
  const counts = { count: 2, districts: { Kegalle: { warning: 1 }, Ratnapura: { imminent: 1 } } };
  const b = await settled(browser({ groups: ["ngo"], api: { "/alerts": counts } }));
  assert.ok(b.calls.includes("public/zones.json") && !b.calls.some((u) => u.endsWith("/zones") && u.startsWith("https")));
  assert.match(b.els.alerts.innerHTML, /2 alerts in the last two days/);
  assert.match(b.els.alerts.innerHTML, /Kegalle: 1 warning/);
});

test("duty officers get approvals and the officer tools", async () => {
  const b = await settled(browser({ groups: ["duty_officer"], api: { "/zones": ZONES, "/alerts": ALERTS, "/approvals": { approvals: [] } } }));
  assert.ok(b.officerSections.every((s) => !s.hidden));
  assert.ok(b.calls.some((u) => u.endsWith("/approvals")));
});

test("the public gets the cached public file and no API calls", async () => {
  const b = await settled(browser({}));
  assert.deepEqual(b.calls.filter((u) => u.startsWith("https://api.test")), []);
  assert.equal(b.layers.Z07.style.fillColor, "#d14b3d"); // warning colour from public/zones.json
});
