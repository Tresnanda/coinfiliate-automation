from __future__ import annotations

from aiohttp import web

_SHOPS_STATE: list = []  # mutated by /admin/sync-shops


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


async def _partner_shop_page(req):
    rows = "".join(
        f'<tr data-cfi="{s["id"]}"><td><a href="/admin/partner-shop/{s["id"]}/edit" class="edit">Edit</a></td>'
        f'<td class="name">{s["name"]}</td><td class="network">{s["network"]}</td>'
        f'<td class="status">{s["status"]}</td></tr>'
        for s in _SHOPS_STATE
    )
    return web.Response(text=f"""
        <html><body>
          <button id="sync-open">Sync Partner Shop</button>
          <div role="dialog" id="sync-modal" style="display:none">
            <label>Network</label>
            <select role="combobox" id="net"><option>flexoffers</option><option>awin</option></select>
            <label>Page</label><input id="page" />
            <label>Page Size</label><input id="page-size" />
            <button id="sync-now">Sync Now</button>
          </div>
          <table><tbody id="rows">{rows}</tbody></table>
          <script>
            document.getElementById("sync-open").onclick = () =>
              document.getElementById("sync-modal").style.display = "block";
            document.getElementById("sync-now").onclick = async () => {{
              await fetch("/admin/sync-shops", {{method: "POST"}});
              location.reload();
            }};
          </script>
        </body></html>
    """, content_type="text/html")


async def _sync_shops(req):
    _SHOPS_STATE.clear()
    _SHOPS_STATE.extend([
        {"id": "cfi-1", "name": "Notch", "network": "flexoffers", "status": "draft"},
        {"id": "cfi-2", "name": "Kryptek", "network": "flexoffers", "status": "draft"},
    ])
    return web.Response(status=204)


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/login", _login_page)
    app.router.add_post("/login", _do_login)
    app.router.add_get("/admin/partner-shop", _partner_shop_page)
    app.router.add_post("/admin/sync-shops", _sync_shops)
    return app
