# Derma City Collector

A field-collection tool for one collector's own book of Derma City receivables.
Reads live from Odoo like the receivables tracker, but everything else here is
built for actually chasing the money down in person: contact people and their
positions, a call/visit log, signed account-reconciliation proof, map
locations, a proximity-based weekly visit plan for Riyadh, a calendar, and an
Arabic script bank for common customer responses.

## What it's for

Your book of ~25 assigned customers is matched by name against the synced
Odoo customers (same fuzzy Arabic matching the receivables tracker uses for
its agency list — spelling variants like ا/أ or ي/ى resolve fine). Everything
in the app — the customer list, the weekly plan, the calendar — is scoped to
that assigned book, not the whole synced ledger.

## Deployed on Vercel

Same architecture as the other two trackers: a Vercel Function backed by
Turso (libSQL), public with no login (your choice — this one holds more
sensitive material than the others: your call notes, promises, and signed
reconciliation photos, so reconsider that if it stops feeling right).

```
api/          Python backend — one Vercel Function (api/index.py)
public/       Frontend — served directly by Vercel
vercel.json   Routes /api/* to the function; 300s max duration (sync is slow)
```

### First-time setup

1. **Turso** — create a database in a group in the same region as your
   Vercel function (see the receivables tracker's README for why this
   matters — a database in the wrong region measurably slows every request).
   ```bash
   turso db create derma-city-collector --group us-east
   turso db show derma-city-collector --url
   turso db tokens create derma-city-collector
   ```
2. **Vercel** — set environment variables and deploy:
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` — from step 1
   - `ODOO_URL`, `ODOO_DB`, `ODOO_USER`, `ODOO_PASSWORD`
   - `CRON_SECRET` — any random string; protects the sync endpoint
   ```bash
   vercel --prod
   ```
3. **Scheduled sync** — `.github/workflows/sync.yml` hits `/api/sync` every
   20 minutes. Add `APP_URL` and `CRON_SECRET` as repo Actions secrets.
4. **Assign your book** — open the app, click **Manage my book**, paste your
   customer names (one per line), and it matches them against the synced
   Odoo customers automatically.

### Local development

```bash
python api/index.py
```

runs the API standalone on `localhost:5090`. Point a static server at
`public/` for the frontend, or just use `vercel dev` to run both together
exactly as they behave in production.

## The weekly plan

Customers need a map location before they can be routed — set one per
customer from their detail panel: paste a Google Maps link, type
`lat, lng`, tap on the embedded map, or tap **Use my location** the first
time you're physically there.

**Generate suggested plan** builds a route with straight-line proximity
clustering (no external routing API, no cost): each day starts at the most
urgent unplanned customer (biggest overdue balance, or a broken promise),
then keeps adding whichever unplanned customer is closest, stopping a day's
route once the next-nearest customer is more than ~12km away rather than
zig-zagging across Riyadh chasing the single most urgent account. Customers
without a location yet are listed separately rather than silently dropped.

The plan is saved, not just computed on the fly — drag a stop to a different
day with its dropdown, or remove it, and it stays that way until you
regenerate.

## The script bank

Seeded with ~20 scripts across common situations (asks to delay, claims
already paid, disputes the amount, goes quiet, asks for installments, a
broken promise, opening a first visit, requesting a signature, and more) —
written for B2B collection with an ongoing customer relationship in mind:
firm on the money, but not scorched-earth. Every script pushes toward a
specific, checkable commitment (a date, an amount, a name) rather than
accepting a vague promise. Add your own from either the Script Bank tab or
directly from the "What do I say?" button while logging a visit.

## Signed reconciliation

Photos are compressed client-side (resized, JPEG-compressed, with the
quality stepped down automatically if still too large) before upload, since
Vercel's request limit is ~4.5MB and a phone camera photo can clear that
easily. Stored directly in the database as base64 — fine at this scale (one
collector's own documents), not meant to become a general document store.
