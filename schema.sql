CREATE TABLE IF NOT EXISTS shop (
    id                   INTEGER PRIMARY KEY,
    coinfiliate_id       TEXT UNIQUE NOT NULL,
    name                 TEXT NOT NULL,
    network              TEXT NOT NULL,
    advertiser_id        TEXT,
    website_url          TEXT,
    edit_url             TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',
    last_error           TEXT,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS affiliate_link (
    id                   INTEGER PRIMARY KEY,
    shop_id              INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
    link_id              TEXT NOT NULL,
    name                 TEXT,
    affiliate_url        TEXT NOT NULL,
    is_harvest_source    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(shop_id, link_id)
);

CREATE TABLE IF NOT EXISTS harvest (
    id                           INTEGER PRIMARY KEY,
    shop_id                      INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
    attempted_at                 TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    final_url                    TEXT,
    final_etld1                  TEXT,
    cookies_json                 TEXT NOT NULL,
    redirect_chain_json          TEXT NOT NULL,
    tracker_domains_json         TEXT NOT NULL,
    primary_cookie_name          TEXT,
    tracking_cookie_names_json   TEXT,
    checkout_domains_json        TEXT,
    tracking_cookie_domains_json TEXT,
    decision_source              TEXT NOT NULL,
    confidence                   REAL,
    llm_rationale                TEXT,
    ok                           INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_shop_status   ON shop(status);
CREATE INDEX IF NOT EXISTS idx_harvest_shop  ON harvest(shop_id, attempted_at DESC);
