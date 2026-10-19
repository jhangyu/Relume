# Minimal APKMirror Turnstile solver service (runs in the CloudflareBypass image: CloakBrowser + Xvfb).
# GET /clearance?url=<download.php URL>&referer=<download page> -> {"ua": ..., "cookies": "k=v; ..."}
# One fresh headed browser per request; clicks the Turnstile checkbox; returns once cf_clearance exists.
# usage: python3 solver.py <port>   (DISPLAY must point at a running Xvfb)
import asyncio, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from cloakbrowser import launch_async

BUDGET = 60
BOX_JS = 'input[name="cf-turnstile-response"] >> xpath=ancestor::div[{}]'


async def widget(page):
    for f in page.frames:
        if "challenges.cloudflare.com" in f.url:
            try:
                b = await (await f.frame_element()).bounding_box()
                if b and b["width"] > 40:
                    return b
            except Exception:
                pass
    for depth in (1, 2, 3):
        loc = page.locator(BOX_JS.format(depth))
        try:
            if await loc.count():
                b = await loc.first.bounding_box(timeout=1000)
                if b and b["width"] > 40 and 20 < b["height"] < 120:
                    return b
        except Exception:
            pass
    return None


async def solve(url, referer):
    browser = await launch_async(headless=False, humanize=True, locale="en-US", timezone="UTC")
    try:
        ctx = await browser.new_context(accept_downloads=True)
        page = await ctx.new_page()
        if referer:
            await page.goto(referer, wait_until="domcontentloaded")
        ua = await page.evaluate("navigator.userAgent")
        try:
            await page.goto(url, wait_until="domcontentloaded")
        except Exception:
            pass  # Chromium raises when the navigation turns into a download
        t0 = time.monotonic()
        while time.monotonic() - t0 < BUDGET:
            cookies = await ctx.cookies("https://" + urlparse(url).hostname + "/")
            if any(c["name"] == "cf_clearance" for c in cookies):
                return {"ua": ua, "cookies": "; ".join(f'{c["name"]}={c["value"]}' for c in cookies)}
            b = await widget(page)
            if b:
                await page.mouse.click(b["x"] + 30, b["y"] + b["height"] / 2)
                await asyncio.sleep(4)
            else:
                await asyncio.sleep(1)
        return None
    finally:
        await browser.close()


class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        res = None
        if urlparse(self.path).path == "/clearance" and q.get("url"):
            try:
                res = asyncio.run(solve(q["url"][0], q.get("referer", [""])[0]))
            except Exception as e:
                self.log_message("solve error: %s", e)
        body = json.dumps(res or {"error": "not solved"}).encode()
        self.send_response(200 if res else 502)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
