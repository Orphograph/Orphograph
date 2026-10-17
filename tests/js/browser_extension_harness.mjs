// Runs dist/browser-extension/background.js in a node vm with a fake
// `chrome` and `fetch`, drives one scenario, prints what was sent as JSON.
// usage: node browser_extension_harness.mjs <background.js> <scenario>
import fs from "node:fs";
import vm from "node:vm";

const [src, scenario] = process.argv.slice(2);
const listeners = {};
const anchors = [];
const windows = [];
const session = {};
const on = (name) => ({ addListener: (fn) => { listeners[name] = fn; }, removeListener() {} });
const chrome = {
  runtime: { id: "ext-self", onInstalled: on("installed"), onMessage: on("message") },
  contextMenus: { create() {}, onClicked: on("clicked") },
  notifications: { create() {}, onClicked: on("notifClicked") },
  storage: {
    local: { get: async () => ({}) },
    session: {
      get: async (k) => (k in session ? { [k]: session[k] } : {}),
      set: async (o) => Object.assign(session, o),
      remove: async (k) => { delete session[k]; },
    },
  },
  windows: { create: async (o) => { windows.push(o.url); } },
  tabs: { create() {} },
};
async function fakeFetch(url, opts = {}) {
  if (String(url).endsWith("/api/anchor")) {
    anchors.push(JSON.parse(opts.body).client_label);
    return { ok: true, status: 200, text: async () => JSON.stringify({ receipt_id: "R1", calendars_ok: 2 }) };
  }
  return { ok: true, status: 200, arrayBuffer: async () => new TextEncoder().encode("bytes").buffer };
}
const ctx = vm.createContext({ chrome, fetch: fakeFetch, crypto: globalThis.crypto, URL, TextEncoder, console });
vm.runInContext(fs.readFileSync(src, "utf8"), ctx);
const settle = () => new Promise((r) => setTimeout(r, 20));
const target = "https://cdn.example/a/holiday%20photo.jpg?token=SECRET#frag";
const askIdOf = () => (windows[0] || "").split("#")[1];

if (scenario === "plain") {
  await listeners.clicked({ menuItemId: "orphograph-anchor", srcUrl: target });
} else {
  await listeners.clicked({ menuItemId: "orphograph-anchor-label", srcUrl: target });
  await settle();
  if (scenario !== "asked-only") {
    const answer = scenario.replace("answer-", "");
    const sender = { id: scenario === "answer-label-foreign" ? "other-ext" : "ext-self" };
    listeners.message({ type: "orpho-ask-answer", askId: askIdOf(), answer: answer.replace("-foreign", "") }, sender);
  }
}
await settle();
console.log(JSON.stringify({ anchors, windows, pending: Object.values(session) }));
