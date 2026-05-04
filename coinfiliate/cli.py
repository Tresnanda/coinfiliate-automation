from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional
import typer
from coinfiliate.config import load_settings
from coinfiliate.store import Store
from coinfiliate.browser import BrowserSession, harvest_browser
from coinfiliate.logging_setup import configure, get_logger, log_file_path

app = typer.Typer(no_args_is_help=True)
log = get_logger(__name__)


def _settings_and_store(config_path: Path, db_path: Path):
    s = load_settings(config_path)
    configure(level=s.logging.level)
    store = Store(db_path)
    return s, store


def _make_llm(settings):
    if settings.llm.provider == "openai":
        from openai import AsyncOpenAI
        from coinfiliate.llm.openai_client import OpenAICookieAnalyzer
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        return OpenAICookieAnalyzer(
            client=client, model=settings.llm.model,
            max_retries=settings.llm.max_retries,
            timeout_seconds=settings.llm.timeout_seconds,
        )
    elif settings.llm.provider == "gemini":
        from google import genai
        from coinfiliate.llm.gemini_client import GeminiCookieAnalyzer
        client = genai.Client(api_key=settings.gemini_api_key)
        return GeminiCookieAnalyzer(
            client=client, model=settings.llm.model,
            max_retries=settings.llm.max_retries,
        )
    raise typer.BadParameter(f"Unknown LLM provider: {settings.llm.provider}")


@app.command()
def sync(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
         limit: Optional[int] = typer.Option(None, help="Cap shops processed; overrides max_shops_per_batch"),
         from_page: Optional[int] = typer.Option(
             None, "--from-page",
             help="1-indexed Partner Shop list page to start from (overrides sync.from_page)",
         ),
         to_page: Optional[int] = typer.Option(
             None, "--to-page",
             help="1-indexed inclusive last page (overrides sync.to_page)",
         )):
    """Pull Partner Shops + affiliate links into SQLite."""
    from coinfiliate.sync import run_sync

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        if from_page is not None:
            s.sync.from_page = from_page
        if to_page is not None:
            s.sync.to_page = to_page
        await store.init()
        try:
            async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
                await run_sync(s, store, ctx)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def harvest(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
            limit: Optional[int] = typer.Option(None)):
    """For each pending shop: open affiliate URL, decide, store harvest row."""
    from coinfiliate.harvest import run_harvest

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        await store.init()
        llm = _make_llm(s)
        try:
            async with harvest_browser(headless=True) as browser:
                await run_harvest(store, settings=s, llm=llm, browser=browser)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def writeback(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
              limit: Optional[int] = typer.Option(None),
              dry_run: bool = typer.Option(False, "--dry-run",
                                           help="Fill fields but cancel instead of saving")):
    """For each harvested shop: drive Edit modal, save, verify."""
    from coinfiliate.writeback import run_writeback

    async def _run():
        s, store = _settings_and_store(config, db)
        if limit is not None:
            s.runner.max_shops_per_batch = limit
        if dry_run:
            s.writeback.verify_after_save = False
        await store.init()
        try:
            async with BrowserSession(Path(".playwright/coinfiliate")) as ctx:
                await run_writeback(store, settings=s, browser_ctx=ctx, dry_run=dry_run)
        finally:
            await store.close()
    asyncio.run(_run())


@app.command()
def run(config: Path = Path("config.yaml"), db: Path = Path("state.db"),
        limit: Optional[int] = typer.Option(None),
        from_page: Optional[int] = typer.Option(
            None, "--from-page",
            help="1-indexed Partner Shop list page to start from (sync phase only)",
        ),
        to_page: Optional[int] = typer.Option(
            None, "--to-page",
            help="1-indexed inclusive last page (sync phase only)",
        ),
        dry_run: bool = typer.Option(False, "--dry-run")):
    """sync -> harvest -> writeback."""
    # Configure logging up-front so the log file path is known before any
    # subcommand body runs, and so we can announce it.
    s, _ = _settings_and_store(config, db)
    log.info(
        "pipeline.start",
        log_file=str(log_file_path()), limit=limit,
        from_page=from_page, to_page=to_page, dry_run=dry_run,
    )
    sync(config=config, db=db, limit=limit, from_page=from_page, to_page=to_page)
    harvest(config=config, db=db, limit=limit)
    writeback(config=config, db=db, limit=limit, dry_run=dry_run)

    async def _summary():
        store = Store(db)
        await store.init()
        try:
            shops = await store.list_shops()
            by_status: dict[str, int] = {}
            for shop in shops:
                by_status[shop["status"]] = by_status.get(shop["status"], 0) + 1
            log.info("pipeline.summary", total=len(shops), by_status=by_status,
                     log_file=str(log_file_path()))
        finally:
            await store.close()
    asyncio.run(_summary())


@app.command()
def doctor(config: Path = Path("config.yaml")):
    """Print every selector key/value pair (live validation is future work)."""
    from coinfiliate.selectors import SELECTORS
    typer.echo("Selectors defined:")
    for k, v in SELECTORS.items():
        typer.echo(f"  {k:40s} {v}")
    typer.echo("\nTo validate live: run against a throwaway shop with --live (not implemented in v1).")


@app.command()
def review(config: Path = Path("config.yaml"), db: Path = Path("state.db")):
    """List needs_review shops for manual decision."""
    async def _run():
        s, store = _settings_and_store(config, db)
        await store.init()
        rows = await store.list_shops(status="needs_review")
        for r in rows:
            latest = await store.latest_harvest(r["id"])
            typer.echo(f"[{r['id']}] {r['name']} ({r['network']}) -- last_error={r['last_error']}")
            if latest:
                typer.echo(f"    decision_source={latest['decision_source']} confidence={latest['confidence']}")
        await store.close()
    asyncio.run(_run())


def main():
    app()


if __name__ == "__main__":
    main()
