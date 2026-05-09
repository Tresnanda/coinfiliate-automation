from __future__ import annotations

from aiohttp import web


# ---- Existing passive flow (preserved so test_harvest_signals.py still passes)

async def _aff_redirect(req):
    raise web.HTTPFound("/tracker")


async def _tracker(req):
    raise web.HTTPFound("/merchant")


async def _merchant(req):
    return web.Response(text="""
        <html><body>
          <div id="consent"><button id="accept">Accept</button></div>
          <script>document.getElementById("accept").onclick = () =>
            document.cookie = "__kla_id=clickid-abc; path=/";
          </script>
        </body></html>
    """, content_type="text/html")


# ---- Active flow: landing (catalog) → PDP → cart → checkout

async def _aff_active(req):
    """Affiliate hop that lands on the catalog page."""
    raise web.HTTPFound("/catalog")


async def _catalog(req):
    return web.Response(text="""
        <html><head><title>Shop Catalog | Fake Store</title></head><body>
          <h1>Shop Catalog</h1>
          <a href="/products/roses-bouquet" id="pdp-link">Roses Bouquet $30</a>
          <a href="/about">About</a>
        </body></html>
    """, content_type="text/html")


async def _pdp(req):
    return web.Response(text="""
        <html><head><title>Roses Bouquet | Fake Store</title></head><body>
          <h1>Roses Bouquet</h1>
          <p>$30</p>
          <form action="/cart/add" method="post">
            <button type="submit" id="add-to-cart">Add to Cart</button>
          </form>
        </body></html>
    """, content_type="text/html")


async def _cart_add(req):
    raise web.HTTPFound("/cart")


async def _cart(req):
    return web.Response(text="""
        <html><head><title>Cart | Fake Store</title></head><body>
          <h1>Your Cart</h1>
          <p>Roses Bouquet — $30</p>
          <a href="/checkouts/cn/abc123" id="checkout-link">Checkout</a>
        </body></html>
    """, content_type="text/html")


async def _checkout(req):
    # Set the affiliate cookie *only* at checkout — proves the cookie capture
    # is happening at the right point.
    resp = web.Response(text="""
        <html><head><title>Checkout | Fake Store</title></head><body>
          <h1>Checkout</h1>
          <p>Total: $30</p>
        </body></html>
    """, content_type="text/html")
    resp.set_cookie("pjnclick", "click-xyz", path="/", domain="127.0.0.1")
    return resp


# ---- Dead link variant (404 landing)

async def _aff_dead(req):
    raise web.HTTPFound("/dead")


async def _dead(req):
    return web.Response(status=404, text="""
        <html><head><title>404 — Page Not Found</title></head>
        <body><h1>Page not found</h1></body></html>
    """, content_type="text/html")


def make_app() -> web.Application:
    app = web.Application()
    # Passive flow (legacy test fixture)
    app.router.add_get("/aff", _aff_redirect)
    app.router.add_get("/tracker", _tracker)
    app.router.add_get("/merchant", _merchant)
    # Active flow
    app.router.add_get("/aff_active", _aff_active)
    app.router.add_get("/catalog", _catalog)
    app.router.add_get("/products/{slug}", _pdp)
    app.router.add_post("/cart/add", _cart_add)
    app.router.add_get("/cart", _cart)
    app.router.add_get("/checkouts/cn/{sid}", _checkout)
    # Dead-link flow
    app.router.add_get("/aff_dead", _aff_dead)
    app.router.add_get("/dead", _dead)
    return app
