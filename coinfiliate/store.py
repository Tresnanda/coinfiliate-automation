from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Sequence
import aiosqlite


SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"

VALID_STATUSES = {"pending", "harvested", "writeback_done", "needs_review", "failed"}

VALID_TRANSITIONS = {
    "pending":        {"harvested", "needs_review", "failed"},
    "harvested":      {"writeback_done", "needs_review", "failed"},
    "needs_review":   {"harvested", "failed"},
    "writeback_done": {"failed"},   # allow retroactive failure marking; otherwise terminal
    "failed":         {"pending", "harvested", "needs_review"},  # allow re-run after fixing
}


class Store:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.executescript(SCHEMA_PATH.read_text())
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """`schema.sql`'s CREATE TABLE IF NOT EXISTS won't add missing columns
        to a pre-existing table; this method ALTERs them in instead.
        """
        cur = await self._conn.execute("PRAGMA table_info(harvest)")
        cols = {row["name"] for row in await cur.fetchall()}
        additions = [
            ("checkout_url", "TEXT"),
            ("checkout_etld1", "TEXT"),
            ("attempted_link_id", "TEXT"),
        ]
        for col, typ in additions:
            if col not in cols:
                await self._conn.execute(f"ALTER TABLE harvest ADD COLUMN {col} {typ}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def upsert_shop(self, *, coinfiliate_id: str, name: str, network: str,
                          advertiser_id: Optional[str], website_url: Optional[str],
                          edit_url: str) -> int:
        cur = await self._conn.execute("SELECT id FROM shop WHERE coinfiliate_id = ?", (coinfiliate_id,))
        row = await cur.fetchone()
        if row:
            await self._conn.execute(
                "UPDATE shop SET name=?, network=?, advertiser_id=?, website_url=?, edit_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (name, network, advertiser_id, website_url, edit_url, row["id"]),
            )
            await self._conn.commit()
            return row["id"]
        cur = await self._conn.execute(
            "INSERT INTO shop (coinfiliate_id, name, network, advertiser_id, website_url, edit_url) VALUES (?, ?, ?, ?, ?, ?)",
            (coinfiliate_id, name, network, advertiser_id, website_url, edit_url),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def update_shop_status(self, shop_id: int, status: str, last_error: Optional[str] = None) -> None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status {status!r}. Must be one of {sorted(VALID_STATUSES)}")
        cur = await self._conn.execute("SELECT status FROM shop WHERE id=?", (shop_id,))
        row = await cur.fetchone()
        if row is None:
            raise ValueError(f"Shop id={shop_id} does not exist")
        current = row["status"]
        if status != current and status not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(f"Illegal status transition {current!r} -> {status!r} for shop id={shop_id}")
        await self._conn.execute(
            "UPDATE shop SET status=?, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, last_error, shop_id),
        )
        await self._conn.commit()

    async def list_shops(self, status: Optional[str] = None) -> list:
        if status:
            cur = await self._conn.execute("SELECT * FROM shop WHERE status=? ORDER BY id", (status,))
        else:
            cur = await self._conn.execute("SELECT * FROM shop ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]

    async def upsert_affiliate_link(self, shop_id: int, *, link_id: str, name: Optional[str], affiliate_url: str) -> None:
        await self._conn.execute(
            """INSERT INTO affiliate_link (shop_id, link_id, name, affiliate_url)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(shop_id, link_id) DO UPDATE SET
                 name=excluded.name, affiliate_url=excluded.affiliate_url""",
            (shop_id, link_id, name, affiliate_url),
        )
        await self._conn.commit()

    async def mark_harvest_source(self, shop_id: int, link_id: str) -> None:
        await self._conn.execute("UPDATE affiliate_link SET is_harvest_source=0 WHERE shop_id=?", (shop_id,))
        await self._conn.execute(
            "UPDATE affiliate_link SET is_harvest_source=1 WHERE shop_id=? AND link_id=?",
            (shop_id, link_id),
        )
        await self._conn.commit()

    async def list_affiliate_links(self, shop_id: int) -> list:
        cur = await self._conn.execute("SELECT * FROM affiliate_link WHERE shop_id=? ORDER BY id", (shop_id,))
        return [dict(r) for r in await cur.fetchall()]

    async def list_affiliate_links_ordered(self, shop_id: int) -> list:
        """Return affiliate links ordered for retry: is_harvest_source first, then by id."""
        cur = await self._conn.execute(
            "SELECT * FROM affiliate_link WHERE shop_id=? "
            "ORDER BY is_harvest_source DESC, id ASC",
            (shop_id,),
        )
        return [dict(r) for r in await cur.fetchall()]

    async def get_harvest_source(self, shop_id: int) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM affiliate_link WHERE shop_id=? AND is_harvest_source=1 LIMIT 1",
            (shop_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None

    async def insert_harvest(self, *, shop_id: int, final_url: Optional[str], final_etld1: Optional[str],
                             cookies: Sequence[dict], redirect_chain: Sequence[str],
                             tracker_domains: Sequence[str], primary_cookie_name: Optional[str],
                             tracking_cookie_names: Optional[Sequence[str]],
                             checkout_domains: Optional[Sequence[str]],
                             tracking_cookie_domains: Optional[Sequence[str]],
                             decision_source: str, confidence: Optional[float],
                             llm_rationale: Optional[str], ok: bool,
                             checkout_url: Optional[str] = None,
                             checkout_etld1: Optional[str] = None,
                             attempted_link_id: Optional[str] = None) -> int:
        cur = await self._conn.execute(
            """INSERT INTO harvest
                 (shop_id, final_url, final_etld1, cookies_json, redirect_chain_json, tracker_domains_json,
                  primary_cookie_name, tracking_cookie_names_json, checkout_domains_json,
                  tracking_cookie_domains_json, decision_source, confidence, llm_rationale, ok,
                  checkout_url, checkout_etld1, attempted_link_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (shop_id, final_url, final_etld1,
             json.dumps(list(cookies)), json.dumps(list(redirect_chain)), json.dumps(list(tracker_domains)),
             primary_cookie_name,
             json.dumps(list(tracking_cookie_names)) if tracking_cookie_names is not None else None,
             json.dumps(list(checkout_domains)) if checkout_domains is not None else None,
             json.dumps(list(tracking_cookie_domains)) if tracking_cookie_domains is not None else None,
             decision_source, confidence, llm_rationale, 1 if ok else 0,
             checkout_url, checkout_etld1, attempted_link_id),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def latest_harvest(self, shop_id: int) -> Optional[dict]:
        cur = await self._conn.execute(
            "SELECT * FROM harvest WHERE shop_id=? ORDER BY attempted_at DESC, id DESC LIMIT 1",
            (shop_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
