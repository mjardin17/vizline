# AGENTS.md — Vigline project conventions

How we work in this repository. These rules are binding for any agent or
human contributing code.

## Money and numbers
- Money is stored and computed as **integer cents**. Never use floats for money.
- Decimal odds are `decimal.Decimal`, constructed from strings, never floats.
- Round half-up to whole cents at payout boundaries; document the rounding.

## Data honesty
- The running app contains **no mock data**. Empty states say what is missing
  and what would provide it.
- Every external observation needs provenance (source URL) and a timestamp.
- Distinguish: observed facts, model estimates (labelled with inputs),
  manually reported records (always unverified), verified results.
- Estimated profit must NEVER be written to realized P&L tables.
- No guaranteed-profit language anywhere in UI copy, API messages, or docs.
- Missing data shows "Unavailable"; never substitute simulated data.

## Promo terms
- `promo_terms_versions` is append-only. Never UPDATE it; INSERT new versions.
- Terms are only fetched from operator-configured official URLs
  (`config/sources.json`, default empty) or manual import. Respect robots.txt.
- Manually entered records are `manually_entered_unverified` and can never be
  relabeled verified by code.

## Scheduler and delivery
- Alert delivery is DISABLED until a destination is configured. Code must make
  it impossible to silently "send" nowhere: `deliver()` without a destination
  raises.
- The scheduler in `alerts/scheduler.py` is a NEW implementation (Oct 2026).
  Never describe it as preserving a historical job.

## Security
- No undocumented scraping, CAPTCHA bypass, credential sharing, login
  automation, or automated wagering. No restriction-evasion tooling.
- Secrets live in environment variables / secret storage, never in code, logs,
  or git history.
- Every state-changing DB write goes through the service layer and writes an
  `audit_log` row (actor + timestamp).

## Tests
- `pytest` must be fully green before any commit or milestone claim.
- New math or state-machine behaviour ships with tests, including the exact
  worked examples in the spec.
- Tests never touch the network.
