# Orphograph — JavaScript Verifier

A standalone, dependency-free JavaScript verifier for Orphograph receipts.
Runs natively in Node 18 or later, and in every modern browser. The entire
verifier is one file, MIT-licensed, intended to be copied directly into any
project that needs to confirm the binding between a file and an Orphograph
receipt without trusting the office that issued it.

## What this verifier does

It recomputes the file's SHA-256 (and, when the receipt carries one,
SHA-512) using the platform Web Cryptography API, and compares the result
against the fingerprints recorded in the receipt JSON. A successful match
establishes that the receipt was issued for that exact byte sequence.

## What this verifier does not do

It does not verify the Bitcoin-chain attestation. The chain verification
is borne by the receipt's `.ots` proof files and requires a Bitcoin node
or an OpenTimestamps client. For full chain-level verification, the
Python verifier at <https://github.com/Orphograph/Orphograph> or any
OpenTimestamps client may be used. The two checks together — the binding
check here, and the chain check there — constitute complete independent
verification of an Orphograph receipt.

## Install

No package manager is required. The verifier is a single file with no
dependencies. One way to install:

```
curl -O https://raw.githubusercontent.com/Orphograph/Orphograph/main/verifier-js/orphograph_verify.js
```

Or copy the file directly from this directory into the project.

## Public API

```js
/**
 * Verify a receipt against the file it claims to attest.
 *
 * @param {Uint8Array | ArrayBuffer | Buffer | Blob | File} fileBytes
 * @param {Object} receipt — the receipt JSON object (must have hash_hex,
 *                          optionally sha512_hex)
 * @returns {Promise<{
 *   ok: boolean,
 *   sha256_match: boolean,
 *   sha512_match: boolean | null,
 *   receipt_id: string,
 *   notes: string[]
 * }>}
 */
export async function verifyReceiptAgainstFile(fileBytes, receipt) { ... }

/**
 * Compute the SHA-256 (and optional SHA-512) of a file. Blob/File inputs
 * are read in 1 MB chunks via Blob.slice() to avoid materialising the
 * entire body at once.
 *
 * @param {Uint8Array | ArrayBuffer | Buffer | Blob | File} fileBytes
 * @param {{ sha512?: boolean, chunkSize?: number }} [opts]
 * @returns {Promise<{ sha256_hex: string, sha512_hex: string | null, size: number }>}
 */
export async function hashFile(fileBytes, opts) { ... }
```

## Node example

The file is an ES module. In Node 18 or later, save it as
`orphograph_verify.js` and import it from a module:

```js
// verify.mjs
import { verifyReceiptAgainstFile } from "./orphograph_verify.js";
import fs from "node:fs";

const fileBytes = fs.readFileSync("photograph.jpg");
const receipt = JSON.parse(fs.readFileSync("receipt.json", "utf-8"));

const result = await verifyReceiptAgainstFile(fileBytes, receipt);
console.log(result);
// {
//   ok: true,
//   sha256_match: true,
//   sha512_match: true,
//   receipt_id: "XwTULwlh76PcCst9",
//   notes: [ ... ]
// }
```

Run it with `node verify.mjs`. For CommonJS callers, load the module
through dynamic `import()`:

```js
const { verifyReceiptAgainstFile } = await import("./orphograph_verify.js");
```

## Browser example

Save the file alongside an HTML document and load it as a module:

```html
<!DOCTYPE html>
<meta charset="utf-8">
<input id="file" type="file">
<input id="receipt" type="file" accept=".json">
<button id="verify">Verify</button>
<pre id="out"></pre>

<script type="module">
  import { verifyReceiptAgainstFile } from "./orphograph_verify.js";

  document.getElementById("verify").addEventListener("click", async () => {
    const file = document.getElementById("file").files[0];
    const receiptFile = document.getElementById("receipt").files[0];
    const receipt = JSON.parse(await receiptFile.text());
    const result = await verifyReceiptAgainstFile(file, receipt);
    document.getElementById("out").textContent = JSON.stringify(result, null, 2);
  });
</script>
```

The file body is read locally; nothing is transmitted to the network.

## For full chain verification

The verifier in this package confirms the file-to-receipt binding. To
confirm that the receipt was committed to the Bitcoin chain, the
receipt's `.ots` proof files must be checked against the chain. The
canonical Python verifier and the underlying OpenTimestamps tooling are
published at <https://github.com/Orphograph/Orphograph> and at
<https://opentimestamps.org/> respectively. Either is sufficient.

## License

MIT. See the accompanying `LICENSE` file.
