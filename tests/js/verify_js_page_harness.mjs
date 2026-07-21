/*!
 * verify_js_page_harness.mjs — executes the REAL inline verifier script from
 * web/verify-js.html inside a minimal DOM stub, so the page's verification
 * logic can be exercised byte-for-byte as shipped (no browser required).
 *
 * The harness drives the same path a reader uses: select a file, paste a
 * receipt, press Verify — then reports the rendered verdict.
 */
import { readFileSync } from "node:fs";
import vm from "node:vm";

function makeEl(id) {
  const el = {
    id,
    disabled: false,
    value: "",
    className: "",
    tagName: "DIV",
    style: {},
    files: null,
    children: [],
    firstChild: null,
    _h: {},
    addEventListener(ev, fn) {
      (this._h[ev] = this._h[ev] || []).push(fn);
    },
    appendChild(c) {
      this.children.push(c);
      return c;
    },
    removeChild() {},
    click() {},
    classList: { add() {}, remove() {} },
  };
  return el;
}

function textOf(node) {
  if (!node) return "";
  if (typeof node.text === "string") return node.text;
  return (node.children || []).map(textOf).join("");
}

const IDS = [
  "fileZone", "filePicker", "fileStatus",
  "receiptZone", "receiptPicker", "receiptStatus", "receiptPaste",
  "verifyBtn", "resetBtn", "result",
  "fetchBtn", "fetchId", "fetchStatus",
];

export function extractInlineScript(htmlPath) {
  const html = readFileSync(htmlPath, "utf8");
  const m = html.match(/<script>\n([\s\S]*?)<\/script>/);
  if (!m) throw new Error("no inline <script> found in " + htmlPath);
  return m[1];
}

/**
 * Run the page's verify flow.
 *
 * @param {string} htmlPath   path to web/verify-js.html
 * @param {Uint8Array} fileBytes  the "selected" file's bytes
 * @param {string} receiptText   the pasted receipt JSON text
 * @returns {Promise<{verdictClass: string, verdict: string, rows: string[]}>}
 */
export async function runPageVerify(htmlPath, fileBytes, receiptText) {
  const src = extractInlineScript(htmlPath);

  const els = {};
  for (const id of IDS) els[id] = makeEl(id);

  const document = {
    getElementById(id) {
      return els[id] || makeEl(id);
    },
    createElement(tag) {
      const el = makeEl("");
      el.tagName = String(tag).toUpperCase();
      return el;
    },
    createTextNode(s) {
      return { text: String(s) };
    },
  };

  class FileReaderStub {
    readAsArrayBuffer(f) {
      queueMicrotask(() => {
        this.result = f._buf;
        if (this.onload) this.onload();
      });
    }
    readAsText(f) {
      queueMicrotask(() => {
        this.result = f._text;
        if (this.onload) this.onload();
      });
    }
  }

  const context = {
    document,
    FileReader: FileReaderStub,
    crypto: globalThis.crypto,
    window: { crypto: globalThis.crypto },
    fetch: () => Promise.reject(new Error("network disabled in harness")),
    encodeURIComponent,
    console,
    Promise,
    Uint8Array,
    JSON,
    Error,
    String,
    setTimeout,
  };
  vm.createContext(context);
  vm.runInContext(src, context, { filename: "verify-js.html<inline>" });

  // Step 1 — select the file (synchronous handler).
  const buf = fileBytes.buffer.slice(
    fileBytes.byteOffset,
    fileBytes.byteOffset + fileBytes.byteLength
  );
  const fileStub = { name: "evidence.bin", size: fileBytes.byteLength, _buf: buf };
  els.filePicker.files = [fileStub];
  for (const fn of els.filePicker._h.change || []) fn();

  // Step 2 — paste the receipt JSON.
  els.receiptPaste.value = receiptText;
  for (const fn of els.receiptPaste._h.input || []) fn();

  if (els.verifyBtn.disabled) {
    return { verdictClass: "not-run", verdict: "verify button stayed disabled", rows: [] };
  }

  // Step 3 — press Verify, then wait for the async render to land.
  els.result.children = [];
  for (const fn of els.verifyBtn._h.click || []) fn();

  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 5));
    if (/valid|mismatch/.test(els.result.className) && els.result.children.length) break;
  }

  const rows = els.result.children.map(textOf);
  const verdictEls = els.result.children.filter(
    (c) => typeof c.className === "string" && c.className.indexOf("v-verdict") === 0
  );
  const last = verdictEls[verdictEls.length - 1];
  return {
    verdictClass: els.result.className,
    verdict: last ? textOf(last) : rows[rows.length - 1] || "",
    rows,
  };
}

export async function sha256Hex(bytes) {
  const d = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha512Hex(bytes) {
  const d = await globalThis.crypto.subtle.digest("SHA-512", bytes);
  return Array.from(new Uint8Array(d)).map((b) => b.toString(16).padStart(2, "0")).join("");
}
