from __future__ import annotations

import json
import pytest
from coinfiliate.store import Store


@pytest.fixture
async def store(tmp_path):
    s = Store(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


async def test_upsert_shop_is_idempotent(store):
    a = await store.upsert_shop(
        coinfiliate_id="cfi-1", name="Notch", network="flexoffers",
        advertiser_id="215583", website_url="https://notchgear.com",
        edit_url="/admin/partner-shop/cfi-1/edit",
    )
    b = await store.upsert_shop(
        coinfiliate_id="cfi-1", name="Notch", network="flexoffers",
        advertiser_id="215583", website_url="https://notchgear.com",
        edit_url="/admin/partner-shop/cfi-1/edit",
    )
    assert a == b
    rows = await store.list_shops()
    assert len(rows) == 1


async def test_upsert_shop_preserves_status(store):
    sid = await store.upsert_shop(coinfiliate_id="x", name="X", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.update_shop_status(sid, "harvested")
    await store.upsert_shop(coinfiliate_id="x", name="X2", network="n",
                            advertiser_id=None, website_url=None, edit_url="/e2")
    rows = await store.list_shops()
    assert rows[0]["status"] == "harvested"
    assert rows[0]["name"] == "X2"


async def test_upsert_affiliate_link_idempotent_and_flags_one_source(store):
    sid = await store.upsert_shop(coinfiliate_id="s", name="S", network="n",
                                  advertiser_id=None, website_url=None, edit_url="/e")
    await store.upsert_affiliate_link(sid, link_id="a", name="A", affiliate_url="u1")
    await store.upsert_affiliate_link(sid, link_id="b", name="B", affiliate_url="u2")
    await store.upsert_affiliate_link(sid, link_id="a", name="A", affiliate_url="u1")
    await store.mark_harvest_source(sid, link_id="a")

    links = await store.list_affiliate_links(sid)
    assert len(links) == 2
    sources = [l for l in links if l["is_harvest_source"] == 1]
    assert len(sources) == 1 and sources[0]["link_id"] == "a"


async def test_insert_harvest_and_list_pending_shops(store):
    s1 = await store.upsert_shop(coinfiliate_id="s1", name="S1", network="n", advertiser_id=None, website_url=None, edit_url="/")
    s2 = await store.upsert_shop(coinfiliate_id="s2", name="S2", network="n", advertiser_id=None, website_url=None, edit_url="/")

    await store.insert_harvest(
        shop_id=s1,
        final_url="https://s.com/",
        final_etld1="s.com",
        cookies=[{"name": "__kla_id", "value": "abc"}],
        redirect_chain=["https://s.com/"],
        tracker_domains=[],
        primary_cookie_name="__kla_id",
        tracking_cookie_names=["__kla_id"],
        checkout_domains=["s.com"],
        tracking_cookie_domains=["s.com"],
        decision_source="heuristic",
        confidence=0.6,
        llm_rationale=None,
        ok=True,
    )
    await store.update_shop_status(s1, "harvested")

    pending = await store.list_shops(status="pending")
    harvested = await store.list_shops(status="harvested")
    assert [r["id"] for r in pending] == [s2]
    assert [r["id"] for r in harvested] == [s1]

    latest = await store.latest_harvest(s1)
    assert latest["primary_cookie_name"] == "__kla_id"
    assert json.loads(latest["cookies_json"])[0]["name"] == "__kla_id"
