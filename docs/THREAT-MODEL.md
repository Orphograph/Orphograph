# What the office can and cannot prove

## In one paragraph

A folder receipt proves that a particular set of files existed at the time
the receipt was issued. It does not prove who created the files, who took
the photos, whether any claim made about the files is true, or that any
file was lawfully obtained. The receipt is a notarised time-stamp on
content the customer already had, not an endorsement.

## In plain English

Things the receipt **can** prove later, to anyone, without the office's
help:

- That the exact files inside the folder existed at the time of the
  receipt (down to the hour, via the Bitcoin chain).
- That a specific file in the folder has not been altered since.
- That the relative path of each file is the path the customer chose at
  anchor time — renaming changes the receipt.
- That a particular file belonged to the folder, while keeping the other
  files in the folder private (the selective-disclosure property).

Things the receipt **cannot** prove:

- Authorship. The receipt proves the file existed, not who made it.
- Truth. If a photo shows something that did not happen, the receipt
  proves only that the photo existed by the anchor time.
- Lawful capture. A photo of someone taken without consent is still a
  photo; the receipt makes no statement about whether the capture was
  lawful.
- That the file is "the original" — only that it existed at the recorded
  time. Earlier copies, if they exist, are also genuine.

## For developers and security reviewers

### The privacy contract

The office never receives file contents. The browser, the Python SDK,
the Node SDK, and the CLI all hash files locally and transmit only the
manifest — a list of relative paths and 32-byte SHA-256 digests — plus
the 32-byte root. Adversaries who compromise the office's storage gain
hashes and paths, not contents.

Paths can themselves be sensitive. Customers handling adversarial
disclosure regimes should sanitise filenames before anchoring or use the
opaque-paths mode (anchor against renamed copies). A future v2 may add
salted-path leaves; the current v1 binds the literal POSIX path.

### Identity binding (optional)

Bitcoin anchoring proves `existed by time T`, not `authored by X`. The
office offers an optional Ed25519 `signature` block on the manifest in
the `did:key` form. When present, the signature is verified at anchor
time; a signature that fails to verify causes the anchor to be rejected.

The signing identity binding is the customer's responsibility. The
office does not issue identities and does not certify the linkage
between a `did:key` and a real-world person or organisation. Two
separate questions live in two separate places:

- "Is this file the one that was anchored at this time?" — answered by
  the receipt and the Bitcoin chain, no third party required.
- "Did this person sign the manifest?" — answered by the optional
  signature block, with whatever trust the verifier has in the
  customer's identity claim.

### Trust assumptions

- **SHA-256.** The receipt's strength is the strength of SHA-256
  pre-image and collision resistance. The office records SHA-512
  sidecars in single-file receipts as a quantum-era hedge; folder
  receipts use SHA-256 inside the tree (per RFC 6962) but the customer
  may anchor an additional SHA-512 root in parallel as a sidecar in
  future versions.
- **OpenTimestamps calendar redundancy.** The office submits each root
  to five independent calendars run by different operators. A receipt
  with three or more successful calendar acknowledgements is considered
  durable. A receipt with fewer is flagged.
- **Bitcoin liveness.** The receipt's time guarantee depends on the
  Bitcoin chain continuing to produce blocks. If the chain stops, the
  receipts already anchored are still verifiable; new anchors would not
  be possible.
- **No private keys held by the office.** The office holds no key that
  would let it forge a customer's receipt. There is nothing to leak.

### Out-of-scope threats

- **Adversary controls the customer's device at anchor time.** The
  receipt only proves what the customer asked the office to anchor; an
  adversary with full device control can anchor anything the customer
  could anchor. The office cannot detect this.
- **Adversary substitutes a different folder before anchor.** Same
  category as above — the office anchors whatever the customer's
  software submits.
- **Receipt-after-the-fact.** If a customer waits a year before
  anchoring a file, the receipt only proves existence at the anchor
  time, not at the time the file was created.

### What an attacker would have to do to forge a receipt

To produce a file that verifies against a receipt the customer did not
make, an attacker would need either:

- a second-preimage collision in SHA-256 (no known practical attack), or
- the customer's optional signing private key (a separate compromise, if
  the customer chose to sign), or
- to fork the Bitcoin chain back to before the receipt's block and
  rewrite it (computationally and economically infeasible while the
  chain has substantial hash power).

None of the three are within reach of normal adversaries.

### Disclosure and limitations

The office is not a law firm, not a regulated medical-records system,
not a qualified electronic trust service, and not a financial advisor.
A receipt is technical evidence; whether it is admissible in a given
forum is a question for that forum's rules of evidence and the customer's
counsel.

## Reporting issues

Security findings should be sent to `security@orphograph.com` with the
office's PGP key (published at `/security.html` once the page is live).
A bug-bounty policy will be published when the office can fund payouts.
