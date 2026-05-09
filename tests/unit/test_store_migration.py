from __future__ import annotations

import aiosqlite
import pytest
from coinfiliate.store import Store


@pytest.mark.unit
async def test_init_adds_new_harvest_columns_to_existing_db(tmp_path):
    # Simulate a pre-migration DB: create harvest WITHOUT the new columns,
    # then run init() and assert the columns now exist.
    db = tmp_path / "old.db"
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE shop (id INTEGER PRIMARY KEY, coinfiliate_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, network TEXT NOT NULL, advertiser_id TEXT, website_url TEXT,
                edit_url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', last_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE harvest (id INTEGER PRIMARY KEY,
                shop_id INTEGER NOT NULL REFERENCES shop(id) ON DELETE CASCADE,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                final_url TEXT, final_etld1 TEXT,
                cookies_json TEXT NOT NULL, redirect_chain_json TEXT NOT NULL,
                tracker_domains_json TEXT NOT NULL, primary_cookie_name TEXT,
                tracking_cookie_names_json TEXT, checkout_domains_json TEXT,
                tracking_cookie_domains_json TEXT, decision_source TEXT NOT NULL,
                confidence REAL, llm_rationale TEXT, ok INTEGER NOT NULL DEFAULT 0);
            """
        )
        await conn.commit()

    store = Store(db)
    await store.init()
    try:
        cur = await store._conn.execute("PRAGMA table_info(harvest)")
        cols = {row["name"] for row in await cur.fetchall()}
        assert {"checkout_url", "checkout_etld1", "attempted_link_id"} <= cols
    finally:
        await store.close()


@pytest.mark.unit
async def test_init_is_idempotent_on_already_migrated_db(tmp_path):
    store = Store(tmp_path / "fresh.db")
    await store.init()
    await store.close()
    # Running init() a second time on a fresh DB must not raise (column already exists).
    store2 = Store(tmp_path / "fresh.db")
    await store2.init()
    try:
        cur = await store2._conn.execute("PRAGMA table_info(harvest)")
        cols = {row["name"] for row in await cur.fetchall()}
        assert {"checkout_url", "checkout_etld1", "attempted_link_id"} <= cols
    finally:
        await store2.close()


@pytest.mark.unit
async def test_list_affiliate_links_ordered_puts_harvest_source_first(tmp_path):
    store = Store(tmp_path / "t.db")
    await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="L1", name="a", affiliate_url="u1")
    await store.upsert_affiliate_link(sid, link_id="L2", name="b", affiliate_url="u2")
    await store.upsert_affiliate_link(sid, link_id="L3", name="c", affiliate_url="u3")
    await store.mark_harvest_source(sid, "L2")

    rows = await store.list_affiliate_links_ordered(sid)
    assert [r["link_id"] for r in rows] == ["L2", "L1", "L3"]
    await store.close()


@pytest.mark.unit
async def test_list_affiliate_links_ordered_empty_shop(tmp_path):
    store = Store(tmp_path / "t.db")
    await store.init()
    sid = await store.upsert_shop(coinfiliate_id="x", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    rows = await store.list_affiliate_links_ordered(sid)
    assert rows == []
    await store.close()
