# Vigline — clean-room rebuild (milestone 1: promo command center)

Vigline is a sports-betting intelligence app. This repository is a **clean-room
rebuild** started October 2026: no code was recovered from any earlier
implementation. Milestone 1 is the **promo command center**: track sportsbook
promotion offers, capture and version their official terms, plan bonus-bet
conversions with exact integer-cent math, manage a promo wallet, and run a
(delivery-disabled) alert scheduler.

Milestone 2 (not started): live odds/EV engines that would feed the scan step.

## Stack rationale

| Choice | Why |
|---|---|
| Python 3.11+ | The brief specified it; exact decimal math via `decimal.Decimal`. |
| FastAPI | The brief specified it; clean JSON API + `TestClient` smoke tests. |
| SQLite + hand-written SQL migrations | Zero services, single file, auditable. `src/vigline/migrations/001_initial.sql` with a tiny runner in `db.py`. |
| Jinja2 server-rendered UI | No build step, no JS framework, works offline; every page degrades to an honest empty state. |
| APScheduler | In-process cron for the scan + expiry workers; schedule defined in code (`alerts/scheduler.py`), never a system cron. |
| pytest | Unit + API tests; 72 tests, all green. |
| httpx | Reserved for authorized terms fetches (milestone 1 only fetches operator-configured URLs). |
| Money as integer cents; odds as `Decimal` from strings | Floats never touch money. Rounding: half-up to whole cents at payout boundaries. |

## Setup

```bash
cd vigline
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# create / migrate the database (default: ./data/vigline.db)
python -m vigline.cli init-db

# run the server
python -m vigline.cli serve --host 127.0.0.1 --port 8000
# or: uvicorn vigline.api.app:app --host 127.0.0.1 --port 8000

# run the tests
pytest
```

Configuration: copy `config/sources.example.json` to `config/sources.json` and add
operator-authorized official terms URLs (default: empty → the app shows
"Unavailable — no authorized source configured"). `VIGLINE_DB_PATH` overrides the
database location.

## What this build does

- **Offer tracking** — CRUD for promo offers with status
  (`unavailable|needs_review|reviewed|expired|changed|inapplicable`), review
  workflow (`retrieved→needs_review→reviewed`), and eligibility
  (`unknown|eligible|ineligible|inapplicable`).
- **Terms capture & versioning** — paste official terms text or fetch from an
  authorized URL (robots.txt respected, allowlist-only). Stored as immutable
  versions with SHA-256; identical re-captures are idempotent; changed text
  flags the offer `changed` and forces re-review.
- **Scenario calculator** — exact conversion math: equal-return hedge sizing,
  10-row scenario matrix (qualifying win/loss/push, bonus issued/expired,
  conversion legs win/push/rejected, costs), peak cash commitment, and
  four offer rankings. Expected-value output requires your explicit probability
  inputs and is labelled `model estimate (inputs: …)`.
- **Hedge guard** — if terms forbid hedging, the math layer refuses to produce
  a hedge plan instead of silently complying.
- **Promo wallet** — restricted credits tracked separately from cash; state
  machine (`pending→issued→used|expired`, `issued→disputed→used|expired`);
  manual entries are permanently `manually_entered_unverified`.
- **Alert scheduler (NEW, Oct 2026)** — APScheduler cron every 2h at
  09,11,13,15,17,19,21,23 America/New_York; dedup by
  (kind, market_key, book, side, price_bucket); repeat alerts suppressed unless
  strictly improved. **Delivery is disabled until a destination is configured**:
  alerts are recorded to the DB with `delivery_disabled`; `deliver()` without a
  destination raises. The scan step is a defined stub returning
  "no data source configured" until milestone 2. A separate promo-expiry worker
  flags wallet rewards expiring within a configurable window (default 72h).
- **Audit log** — every state-changing write records actor + timestamp.

## Honest feature matrix

| Feature | Implemented | Tested | Connected | Validated |
|---|---|---|---|---|
| Offer CRUD + review/eligibility workflow | ✅ | ✅ (unit + API) | n/a (local) | ✅ local run |
| Terms capture/versioning/diff flagging | ✅ | ✅ | ⬜ only with user-configured authorized URLs | ✅ local run |
| Authorized-URL fetch (robots.txt, allowlist) | ✅ | ✅ (mocked fetch) | ⬜ no sources configured by default | ⬜ not against a live operator page |
| Scenario calculator + hedge math | ✅ | ✅ incl. worked examples | n/a | ✅ local run |
| Hedge refusal on restrictive terms | ✅ | ✅ | n/a | ✅ local run |
| Four offer rankings | ✅ | ✅ | n/a | ✅ local run |
| Wallet state machine | ✅ | ✅ | n/a | ✅ local run |
| Alert scheduler + dedup + expiry worker | ✅ | ✅ incl. ET cron timing | ⬜ delivery disabled by design | ✅ local run |
| Alert delivery (email/SMS/push) | ❌ not built — disabled by design | ✅ (raises without destination) | ❌ no destination configured | ❌ |
| Odds/EV engines + live scan data | ❌ milestone 2 | stub only | ❌ | ❌ |
| Real-money wagering / betting automation | ❌ never — out of scope by user constraint | n/a | ❌ | ❌ |

Legend: ✅ done · ⬜ needs user input / future work · ❌ not present.

## Rules this build follows (see AGENTS.md)

- No mock data anywhere in the running app; empty states say what is missing.
- Every external observation carries source + timestamp.
- Estimates are labelled; only settled results count as profit. Estimated profit
  never enters realized P&L (enforced by schema `CHECK` + a dedicated test).
- No guaranteed-profit language in UI, API, or docs.
- Secrets stay in the environment, never in code or git.
- The scheduler is a new Oct-2026 implementation; it is never described as a
  preserved historical job.
