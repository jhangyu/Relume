import pathlib, re, unittest
from webui import server

STATIC = pathlib.Path(server.__file__).with_name('static')
UNSAFE = re.compile(r'(?:innerHTML|outerHTML)\s*=(?!\s*(?:html`|\'\'|""|``))|insertAdjacentHTML\([^,]+,(?!\s*html`)')


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
