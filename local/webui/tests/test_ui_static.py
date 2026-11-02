import pathlib, re, unittest
from webui import server

STATIC = pathlib.Path(server.__file__).with_name('static')
UNSAFE = re.compile(r'(?:innerHTML|outerHTML)\s*=(?!\s*(?:html`|\'\'|""|``))|insertAdjacentHTML\([^,]+,(?!\s*html`)')
SWALLOW = re.compile(r"\.catch\(\s*\(\s*\w*\s*\)\s*=>\s*(?:\{\s*\}|null|undefined)\s*\)|catch\s*\(\s*_\s*\)")
RAW_TOAST = re.compile(r"toast\(\s*\w+\.message\s*,\s*'error'\s*\)")   # failures go through report()/act(), which skip a 401


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

    def test_errors_are_not_swallowed_or_toasted_raw(self):
        self.assertTrue(SWALLOW.search('x().catch(() => {})') and SWALLOW.search('} catch (_) {') and RAW_TOAST.search("toast(e.message, 'error')"))
        for f in sorted(STATIC.glob('*.js')):
            self.assertEqual(SWALLOW.findall(f.read_text()), [], f.name)
            if f.name != 'app.js':   # app.js holds act()/report(), the one place allowed to toast a raw message
                self.assertEqual(RAW_TOAST.findall(f.read_text()), [], f.name)
