import pathlib, re, unittest
from webui import server
from webui.tests.helpers import DataTestCase, serve, http, login

STATIC = pathlib.Path(server.__file__).with_name('static')
UNSAFE = re.compile(r'(?:innerHTML|outerHTML)\s*=(?!\s*(?:html`|\'\'|""|``))|insertAdjacentHTML\([^,]+,(?!\s*html`)')
NAV = ('href="/"', 'href="/builds.html"', 'href="/schedules.html"', 'href="/devices.html"', 'href="/settings.html"')


def violations(text):
    return [m.group(0) for m in UNSAFE.finditer(text)]


class StaticRulesTest(unittest.TestCase):
    def test_negative_fixture_is_flagged(self):
        self.assertTrue(violations('el.innerHTML = `<b>${name}</b>`'))
        self.assertTrue(violations("el.insertAdjacentHTML('beforeend', '<i>' + x)"))
        self.assertFalse(violations('el.innerHTML = html`<b>${name}</b>`'))
        self.assertFalse(violations("el.innerHTML = ''"))

    def test_js_uses_html_template_only(self):
        for f in sorted(STATIC.glob('*.js')):
            self.assertEqual(violations(f.read_text()), [], f.name)
        for f in sorted(STATIC.glob('*.js')):
            if f.name != 'app.js':
                self.assertNotIn('new Safe', f.read_text(), f.name)

    def test_no_network_refs_or_mockup_names(self):
        for f in sorted(STATIC.iterdir()):
            t = f.read_text()
            self.assertNotRegex(t, r'(?:src|href)=["\']https?://|url\(["\']?https?://|fetch\(["\'`]https?://', f.name)
            self.assertNotRegex(t, r'href="(apps|app-detail|build-log|index)\.html', f.name)
            self.assertNotIn('data-toast=', t, f.name)

    def test_pages_have_nav_and_logout(self):
        for f in sorted(STATIC.glob('*.html')):
            t = f.read_text()
            if f.name == 'login.html':
                continue
            for n in NAV:
                self.assertIn(n, t, f'{f.name} {n}')
            self.assertIn('data-logout', t, f.name)
            self.assertIn('id="toast"', t, f.name)


class StaticServeTest(DataTestCase):
    def test_every_page_and_asset(self):
        base = serve(self)
        pages = sorted(STATIC.glob('*.html'))
        self.assertTrue(pages)
        for f in pages:
            path = '/' if f.name == 'index.html' else '/' + f.name
            st, h, _ = http('GET', base + path)
            if f.name == 'login.html':
                self.assertEqual(st, 200)
            else:
                self.assertEqual((st, h.get('Location')), (302, '/login.html'), f.name)
            for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', f.read_text()):
                self.assertEqual(http('GET', base + ref)[0], 200, f'{f.name} {ref}')
        cookie = login(base)
        for f in pages:
            path = '/' if f.name == 'index.html' else '/' + f.name
            self.assertEqual(http('GET', base + path, cookie=cookie)[0], 200, f.name)
