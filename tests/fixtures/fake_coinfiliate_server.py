from __future__ import annotations

from aiohttp import web


async def _login_page(req):
    return web.Response(text="""
        <html><body>
          <form method="post" action="/login">
            <input type="email" name="email" />
            <input type="password" name="password" />
            <button type="submit">Sign in</button>
          </form>
        </body></html>
    """, content_type="text/html")


async def _do_login(req):
    raise web.HTTPFound("/admin/partner-shop")


async def _admin_page(req):
    return web.Response(text="<html><body><h1>Partner Shop</h1></body></html>",
                        content_type="text/html")


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/login", _login_page)
    app.router.add_post("/login", _do_login)
    app.router.add_get("/admin/partner-shop", _admin_page)
    return app
