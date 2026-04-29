from __future__ import annotations

from aiohttp import web


async def _aff_redirect(req):
    # Simulates an affiliate network redirecting to the final merchant with a tracker domain hop.
    raise web.HTTPFound("/tracker")


async def _tracker(req):
    raise web.HTTPFound("/merchant")


async def _merchant(req):
    # Page sets a Klaviyo-style cookie when the consent banner is accepted.
    return web.Response(text="""
        <html><body>
          <div id="consent"><button id="accept">Accept</button></div>
          <script>document.getElementById("accept").onclick = () =>
            document.cookie = "__kla_id=clickid-abc; path=/";
          </script>
        </body></html>
    """, content_type="text/html")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/aff", _aff_redirect)
    app.router.add_get("/tracker", _tracker)
    app.router.add_get("/merchant", _merchant)
    return app
