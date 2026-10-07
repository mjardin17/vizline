-- 001_initial: milestone 1 schema (unified promo command center + alert store).
-- Money is stored as integer cents. Decimal odds are stored as TEXT (exact
-- decimal strings) wherever persisted. promo_terms_versions is append-only:
-- the current version is max(version_no) per offer; never UPDATE it.

CREATE TABLE IF NOT EXISTS promo_sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    platform    TEXT NOT NULL,
    source_url  TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('official_page', 'authorized_feed', 'manual_import')),
    jurisdiction TEXT,
    added_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_offers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id       INTEGER REFERENCES promo_sources(id),
    platform        TEXT NOT NULL,
    title           TEXT NOT NULL,
    offer_type      TEXT NOT NULL,
    scope           TEXT NOT NULL DEFAULT 'public' CHECK (scope IN ('public', 'account_specific')),
    status          TEXT NOT NULL DEFAULT 'needs_review'
                        CHECK (status IN ('unavailable', 'needs_review', 'reviewed', 'expired', 'changed', 'inapplicable')),
    review_state    TEXT NOT NULL DEFAULT 'retrieved'
                        CHECK (review_state IN ('retrieved', 'needs_review', 'reviewed')),
    eligibility_state TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (eligibility_state IN ('unknown', 'eligible', 'ineligible', 'inapplicable')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promo_offers_platform ON promo_offers(platform);
CREATE INDEX IF NOT EXISTS idx_promo_offers_status ON promo_offers(status);

-- Append-only: INSERT new versions only. Current version = max(version_no).
CREATE TABLE IF NOT EXISTS promo_terms_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id        INTEGER NOT NULL REFERENCES promo_offers(id) ON DELETE CASCADE,
    version_no      INTEGER NOT NULL,
    raw_text        TEXT NOT NULL,
    text_hash_sha256 TEXT NOT NULL,
    source_url      TEXT,
    fetched_at      TEXT NOT NULL,
    fetched_by      TEXT NOT NULL,
    UNIQUE (offer_id, version_no),
    UNIQUE (offer_id, text_hash_sha256)
);
CREATE INDEX IF NOT EXISTS idx_terms_offer ON promo_terms_versions(offer_id);

CREATE TABLE IF NOT EXISTS eligibility_rules (
    offer_id    INTEGER PRIMARY KEY REFERENCES promo_offers(id) ON DELETE CASCADE,
    rules_json  TEXT NOT NULL,
    verified    INTEGER NOT NULL DEFAULT 0 CHECK (verified IN (0, 1)),
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS promo_wallet (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    reward_type     TEXT NOT NULL,
    face_value_cents INTEGER NOT NULL CHECK (face_value_cents >= 0),
    state           TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'issued', 'used', 'expired', 'disputed')),
    cash_vs_credit  TEXT NOT NULL CHECK (cash_vs_credit IN ('restricted_credit', 'cash')),
    issued_at       TEXT,
    expires_at      TEXT,
    source          TEXT NOT NULL CHECK (source IN ('verified', 'manually_entered_unverified')),
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wallet_state ON promo_wallet(state);
CREATE INDEX IF NOT EXISTS idx_wallet_expires ON promo_wallet(expires_at);

CREATE TABLE IF NOT EXISTS promo_allocations (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    offer_id                    INTEGER NOT NULL REFERENCES promo_offers(id) ON DELETE CASCADE,
    plan_json                   TEXT NOT NULL,
    peak_cash_commitment_cents  INTEGER,
    status                      TEXT NOT NULL DEFAULT 'planned'
                                    CHECK (status IN ('planned', 'executing', 'completed', 'abandoned')),
    created_at                  TEXT NOT NULL
);

-- Manually reported executions are ALWAYS unverified (enforced by CHECK).
CREATE TABLE IF NOT EXISTS promo_executions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    allocation_id   INTEGER NOT NULL REFERENCES promo_allocations(id) ON DELETE CASCADE,
    report_json     TEXT NOT NULL,
    reported_at     TEXT NOT NULL,
    verified        INTEGER NOT NULL DEFAULT 0 CHECK (verified = 0)
);

-- Realized P&L comes ONLY from settled executions. Estimated/modelled profit
-- must never be written here (enforced by application layer + tests).
CREATE TABLE IF NOT EXISTS promo_settlements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id        INTEGER NOT NULL REFERENCES promo_executions(id) ON DELETE CASCADE,
    realized_net_cents  INTEGER NOT NULL,
    settled_at          TEXT NOT NULL,
    note                TEXT
);

-- Alert dedup identity: (kind, market_key, book, side, price_bucket).
-- A strictly improved price produces a new row (new price_bucket); repeats are
-- suppressed. delivery_status starts at 'delivery_disabled' until a destination
-- is configured.
CREATE TABLE IF NOT EXISTS alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL
                        CHECK (kind IN ('arbitrage', 'positive_ev', 'promo_expiry', 'terms_changed')),
    market_key      TEXT NOT NULL,
    book            TEXT NOT NULL,
    side            TEXT NOT NULL,
    price_bucket    TEXT NOT NULL,
    details_json    TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'delivery_disabled'
                        CHECK (delivery_status IN ('delivery_disabled', 'delivered', 'failed')),
    superseded_by   INTEGER REFERENCES alerts(id),
    created_at      TEXT NOT NULL,
    UNIQUE (kind, market_key, book, side, price_bucket)
);
CREATE INDEX IF NOT EXISTS idx_alerts_kind ON alerts(kind);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id   INTEGER NOT NULL,
    action      TEXT NOT NULL,
    actor       TEXT NOT NULL,
    details_json TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id);
