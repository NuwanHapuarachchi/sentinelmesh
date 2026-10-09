// Runs web/phone.js in a stub browser (node:vm) and checks the two delivery channels.
//   node --test tests/web/*.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../web/phone.js", import.meta.url), "utf8");
const ZONES = { features: [{ geometry: { coordinates: [[[80.4, 6.7]]] }, properties: { zone_id: "Z07", name: "Ratnapura 07", is_test: false } }] };

function element() {
  return { textContent: "", innerHTML: "", hidden: false, value: "", disabled: false, className: "", onclick: null };
}

function browser({ broadcast = null, inbox = [] } = {}) {
  const els = new Proxy({}, { get: (t, id) => (t[id] ??= element()) });
  const storage = new Map();
  const calls = [];
  const reloaded = [];
  const json = (body, ok = true, status = 200) => Promise.resolve({ ok, status, json: () => Promise.resolve(body) });
  const fetch = (url, opts = {}) => {
    calls.push({ url, opts });
    if (url === "config.json") return json({ api_url: "https://api.test/v1" });
    if (url === "zones.geojson") return json(ZONES);
    if (url.startsWith("cb/")) return broadcast ? json(broadcast) : json({}, false, 403);
    if (url.includes("/phone/verify/start")) return json({ sent: false, simulator_code: "123456" });
    if (url.includes("/phone/verify/finish")) return json({ verify_token: "9999999999.mac" });
    if (url.includes("/phone/subscribe")) return json(opts.method === "DELETE" ? { unsubscribed: true } : { subscriber_id: "sim-1", sms: true });
    if (url.includes("/phone/inbox")) return json({ zone_id: "Z07", messages: inbox });
    if (url.includes("/phone/ack")) return json({ acknowledged: true, ttfa_ms: 4200 });
    throw new Error(`unexpected fetch ${url}`);
  };
  const context = vm.createContext({
    document: { getElementById: (id) => els[id] },
    localStorage: { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, v), removeItem: (k) => storage.delete(k) },
    fetch, setInterval: () => 0, location: { search: "", reload() { reloaded.push(true); } }, navigator: {},
    crypto: globalThis.crypto, URLSearchParams, Date, JSON, Number, String, Set, Map, Uint32Array, Promise, console,
  });
  vm.runInContext(SOURCE, context);
  return { context, els, calls, storage, reloaded };
}

const settle = () => new Promise((r) => setTimeout(r, 0));

async function booted(opts) {
  const b = browser(opts);
  for (let i = 0; i < 5; i++) await settle();
  return b;
}

const NOW_S = () => Math.floor(Date.now() / 1000);
const BROADCAST = () => ({
  alert_id: "SM-1", tier: "imminent", status: "Exercise", broadcast_at: NOW_S(),
  texts: { en: "EMERGENCY Z07: Flood imminent.", si: "හදිසි Z07", ta: "அவசரம் Z07" },
});

test("broadcast-only phone shows the area's broadcast without signing up", async () => {
  const b = await booted({ broadcast: BROADCAST() });
  b.els.lang.value = "si";
  b.els.zone.value = "Z07";
  b.els.listen.onclick();
  for (let i = 0; i < 5; i++) await settle();
  assert.equal(b.els.alert.hidden, false);
  assert.equal(b.els.alerttext.textContent, "හදිසි Z07");
  assert.match(b.els.alertmeta.textContent, /^Cell broadcast\./);
  assert.ok(!b.calls.some((c) => c.url.includes("/phone/")), "a broadcast-only phone calls no API");
  assert.match(b.els.history.innerHTML, /Cell broadcast · Exercise/);
});

test("nothing is shown before anything is broadcast", async () => {
  const b = await booted();
  b.els.zone.value = "Z07";
  b.els.listen.onclick();
  for (let i = 0; i < 5; i++) await settle();
  assert.equal(b.els.alerttext.textContent, "", "no alert was opened");
  assert.match(b.els.history.innerHTML, /No alerts yet/);
});

test("an old broadcast is not shown as a new emergency", async () => {
  const b = await booted({ broadcast: { ...BROADCAST(), broadcast_at: NOW_S() - 7 * 3600 } });
  b.els.zone.value = "Z07";
  b.els.listen.onclick();
  for (let i = 0; i < 5; i++) await settle();
  assert.equal(b.els.alerttext.textContent, "");
});

test("an alert arriving by broadcast and SMS pops up once and the SMS is still acknowledged", async () => {
  const inbox = [{ sk: `${Date.now()}#SM-1`, alert_id: "SM-1", tier: "imminent", text: "EMERGENCY by SMS", channel: "sim", status: "Exercise" }];
  const b = await booted({ broadcast: BROADCAST(), inbox });
  b.els.lang.value = "en";
  b.els.zone.value = "Z07";
  await b.els.join.onclick();
  for (let i = 0; i < 10; i++) await settle();
  assert.equal(b.els.alerttext.textContent, "EMERGENCY Z07: Flood imminent.", "first channel wins; SMS does not re-open it");
  assert.ok(b.calls.some((c) => c.url.endsWith("/phone/ack")), "SMS receipt still confirmed");
  const history = b.els.history.innerHTML;
  assert.match(history, /EMERGENCY by SMS/);
  assert.match(history, /Cell broadcast/);
});

test("an all-clear broadcast is shown as all clear", async () => {
  const b = await booted({ broadcast: { ...BROADCAST(), alert_id: "SM-2", tier: "clear", texts: { en: "ALL CLEAR Z07", si: "x", ta: "y" } } });
  b.els.lang.value = "en";
  b.els.zone.value = "Z07";
  b.els.listen.onclick();
  for (let i = 0; i < 5; i++) await settle();
  assert.equal(b.els.alert.className, "alert clear");
  assert.match(b.els.alerthead.textContent, /^All clear/);
});

test("signing up with a number verifies it first, then stores it", async () => {
  const b = await booted();
  b.els.zone.value = "Z07";
  b.els.lang.value = "en";
  b.els.msisdn.value = "0771234567";
  await b.els.join.onclick(); // sends the code
  assert.equal(b.els.codebox.hidden, false);
  assert.match(b.els.codehint.textContent, /123456/);
  assert.ok(!b.calls.some((c) => c.url.includes("/phone/subscribe")), "no sign-up before the code is confirmed");
  b.els.code.value = "123456";
  await b.els.join.onclick(); // checks the code and signs up with the number
  for (let i = 0; i < 5; i++) await settle();
  const finish = b.calls.find((c) => c.url.includes("/phone/verify/finish"));
  assert.deepEqual(JSON.parse(finish.opts.body), { msisdn: "0771234567", code: "123456" });
  const sub = JSON.parse(b.calls.find((c) => c.url.includes("/phone/subscribe")).opts.body);
  assert.equal(sub.msisdn, "0771234567");
  assert.equal(sub.verify_token, "9999999999.mac");
  assert.match(b.els.homestatus.textContent, /SMS to your number/);
  assert.equal(b.els.stop.hidden, false);
});

test("stopping warnings erases the record on the server and forgets the phone", async () => {
  const b = await booted();
  b.els.zone.value = "Z07";
  await b.els.join.onclick();
  for (let i = 0; i < 5; i++) await settle();
  await b.els.stop.onclick();
  const del = b.calls.find((c) => c.opts.method === "DELETE");
  assert.match(del.url, /\/phone\/subscribe\?subscriber_id=sim-1$/);
  assert.equal(del.opts.headers["X-Device-Token"].length > 0, true);
  assert.equal(b.storage.get("sm-phone"), undefined);
  assert.equal(b.reloaded.length, 1);
});

test("a broadcast-only phone has nothing to unsubscribe", async () => {
  const b = await booted();
  b.els.zone.value = "Z07";
  b.els.listen.onclick();
  assert.equal(b.els.stop.hidden, true);
});
