import pathlib, re, unittest
from webui import server
from webui.tests.helpers import DataTestCase, serve, http, login

STATIC = pathlib.Path(server.__file__).with_name('static')
UNSAFE = re.compile(r'(?:innerHTML|outerHTML)\s*=(?!\s*(?:html`|\'\'|""|``))|insertAdjacentHTML\([^,]+,(?!\s*html`)')
NAV = ('href="/"', 'href="/projects.html"', 'href="/builds.html"', 'href="/schedules.html"', 'href="/devices.html"', 'href="/settings.html"')


TEXT_SUFFIXES = {'.html', '.css', '.js', '.svg', '.webmanifest'}


def text_assets():
    return sorted(f for f in STATIC.iterdir() if f.suffix in TEXT_SUFFIXES)


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
        for f in text_assets():
            t = f.read_text()
            self.assertNotRegex(t, r'(?:src|href)=["\']https?://|url\(["\']?https?://|fetch\(["\'`]https?://', f.name)
            self.assertNotRegex(t, r'href="(apps|app-detail|build-log|index)\.html', f.name)
            self.assertNotIn('data-toast=', t, f.name)

    def test_index_source_controls(self):
        js = (STATIC / 'index.js').read_text()
        for needle in ("'PUT', `/api/sources/", "'DELETE', `/api/sources/", 'engine_options', 'sigbypass', 'injectdex'):
            self.assertIn(needle, js, needle)
        self.assertIn('id="a-src-edit"', (STATIC / 'index.html').read_text())

    def test_pages_have_nav_and_logout(self):
        js = (STATIC / 'app.js').read_text()
        for n in NAV:
            self.assertIn(n, js, f'app.js {n}')
        self.assertIn('data-logout', js)
        for f in sorted(STATIC.glob('*.html')):
            if f.name == 'login.html':
                continue
            t = f.read_text()
            self.assertIn('<aside class="side" aria-label="Primary"></aside>', t, f.name)
            self.assertIn('id="toast"', t, f.name)

    def test_every_page_links_manifest(self):
        pages = sorted(STATIC.glob('*.html'))
        self.assertEqual(len(pages), 8)
        for f in pages:
            self.assertEqual(f.read_text().count('<link rel="manifest" href="/static/manifest.webmanifest">'), 1, f.name)

    def test_no_dialog_form_min_width(self):
        for f in sorted(STATIC.glob('*.html')):
            for dlg in re.findall(r'<dialog\b.*?</dialog>', f.read_text(), re.S):
                self.assertNotRegex(dlg, r'style="[^"]*min-width', f.name)

    def test_projects_page_uses_project_api(self):
        js = (STATIC / 'projects.js').read_text()
        for needle in ("'/api/projects'", '/api/projects/assets', '/signer/accept', '/install', '/api/upstreams/', "'/api/devices'", 'kind=ota'):
            self.assertIn(needle, js, needle)

    def test_settings_lists_signer_event(self):
        self.assertIn('value="project.signer_changed"', (STATIC / 'settings.html').read_text())
        self.assertIn('project.signer_changed', (STATIC / 'settings.js').read_text())

    def test_no_dead_dependency_model_leftovers(self):
        for f in text_assets():
            t = f.read_text()
            for needle in ('depend' + 'ency-apps', 'depend' + 'ency_app', 'd.' + 'dependencies'):  # split so the dead-model grep skips this line
                self.assertNotIn(needle, t, f'{f.name} {needle}')


class PageIdsTest(unittest.TestCase):
    def test_every_id_a_page_script_looks_up_exists_in_its_html(self):
        pages = [j.stem for j in sorted(STATIC.glob('*.js')) if j.name != 'app.js' and j.with_suffix('.html').exists()]
        self.assertTrue(pages)
        for page in pages:
            js = (STATIC / f'{page}.js').read_text()
            markup = (STATIC / f'{page}.html').read_text()
            ids = set(re.findall(r"\$\$?\(['\"`]#([\w-]+)", js)) | set(re.findall(r"getElementById\(['\"`]([\w-]+)['\"`]", js))
            self.assertTrue(ids, page)
            for i in sorted(ids):
                self.assertIn(f'id="{i}"', markup, f'{page}: #{i}')

    def test_item_page_has_no_mockup_leftovers(self):
        for name in ('item.html', 'item.js'):
            t = (STATIC / name).read_text()
            self.assertNotRegex(t, r'patchName|src/option[s]|shared by every item', name)
        self.assertNotRegex((STATIC / 'builds.html').read_text(), r'Patch 4 / 41|patch update 1\.10\.0')

    def test_item_js_keeps_colour_picker_and_version_labels(self):
        t = (STATIC / 'item.js').read_text()
        for needle in ('type="color"', 'colorInput', 'not in patch list', 'sigbypass'):
            self.assertIn(needle, t)
        self.assertGreaterEqual(t.count('/api/apps/'), 2)
        b = (STATIC / 'builds.js').read_text()
        self.assertIn('logStream', b)
        self.assertIn('::step::', b)


class StaticServeTest(DataTestCase):
    def test_every_page_and_asset(self):
        base = serve(self)
        pages = sorted(STATIC.glob('*.html'))
        self.assertTrue(pages)
        fetched = {}  # each shared asset is requested once, with the first page that references it
        for f in pages:
            path = '/' if f.name == 'index.html' else '/' + f.name
            st, h, _ = http('GET', base + path)
            if f.name == 'login.html':
                self.assertEqual(st, 200)
            else:
                self.assertEqual((st, h.get('Location')), (302, '/login.html'), f.name)
            for ref in re.findall(r'(?:src|href)="(/static/[^"]+)"', f.read_text()):
                if ref not in fetched:
                    fetched[ref] = http('GET', base + ref)[0]
                self.assertEqual(fetched[ref], 200, f'{f.name} {ref}')
        cookie = login(base)
        for f in pages:
            path = '/' if f.name == 'index.html' else '/' + f.name
            self.assertEqual(http('GET', base + path, cookie=cookie)[0], 200, f.name)
