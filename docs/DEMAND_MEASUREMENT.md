# Demand measurement contract

Orphograph must not count its own office automations as customer demand.
Server-side demand events therefore use a closed schema and classify each
successful anchor from verified authentication facts.

```text
request -> verified auth -> origin class -> privacy-safe event ledger
                         \-> receipt (unchanged)

office API-key digest -> office_automation -> excluded from external demand
paid/customer auth    -> external_authenticated
no auth               -> external_anonymous
missing evidence      -> unknown, never guessed
```

## Required production configuration

- `ORPHO_ANALYTICS_HMAC_SECRET`: a dedicated random secret used to create a
  monthly rotating cohort identifier. If absent, no demand ledger is written
  and the dashboard reports `unavailable`, not zero.
- `ORPHO_INTERNAL_API_KEY_HASHES`: comma-separated SHA-256 digests of every
  office-only API key, including the credential used by scheduled anchor jobs.
  Do not place raw credentials in this variable.
- `ORPHO_OFFER_VERSION`: a short lowercase experiment/control identifier such
  as `control-v1`. Invalid values are recorded as `invalid`.
- `ORPHO_DEMAND_EVENTS` (optional): demand-ledger path. It defaults inside the
  configured Orphograph data directory.

Generate an office-key digest without copying the key into shell history:

```sh
python tools/hash_internal_api_key.py
```

The event ledger never stores API keys, email addresses, receipt ids, document
hashes, full IP addresses, or paths. Analytics write failures never fail an
anchor or payment operation.

## Historical baseline

Old receipts do not contain enough evidence to reconstruct customer identity.
Run the read-only classifier against a receipt tree and provide known office
source prefixes only when they can be proved from the old automation config:

```sh
python tools/classify_historical_demand.py /path/to/receipts \
  --office-source-prefix api:knownprefix
```

The report has three confidence bands: `confirmed_office`, `confirmed_external_paid`,
and `unknown`. Free and ambiguous API-key receipts remain unknown. The tool does
not alter receipts or produce migration data.

## Launch gate

Before interpreting conversion, confirm the dashboard says `complete`, office
volume is nonzero when office jobs run, and a controlled external test appears
only in external demand. Keep SCALE-1 through SCALE-5 gated until measured
external demand reaches the thresholds in `docs/SCALE_GATES.md`.
