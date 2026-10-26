# Inlines icons.svg, web/c3.css or android/m3.css, and the web nav into every mockup page.
# Run after editing a shared part: python3 inline.py . (idempotent, replaces between <!--X--> markers)
import pathlib, re, sys
root = pathlib.Path(sys.argv[1])
icons = (root / 'icons.svg').read_text()
icons = re.sub(r'<!--.*?-->\n', '', icons)
web_css = (root / 'web/c3.css').read_text()
and_css = (root / 'android/m3.css').read_text() if (root / 'android/m3.css').exists() else ''
NAV = [('marketplace', 'Marketplace', 'i-store', ''), ('apps', 'Apps', 'i-apps', '<span class="count" aria-label="2 need attention">2</span>'),
       ('restyle-sample', 'Builds', 'i-builds', ''), ('devices', 'Devices', 'i-phone', ''), ('projects', 'Follow projects', 'i-follow', ''),
       ('schedules', 'Schedules', 'i-calendar', ''), ('settings', 'Settings', 'i-settings', '')]

def nav(cur):
    a = []
    for i, (href, label, ic, extra) in enumerate(NAV):
        if i == 2:
            a.append('<div class="nav-h">Server</div>')
        cur_attr = ' aria-current="page"' if href == cur else ''
        url = f'{href}.html' if (root / 'web' / f'{href}.html').exists() else '#'  # kept page not mocked here
        a.append(f'<a href="{url}"{cur_attr}><svg class="i" aria-hidden="true"><use href="#{ic}"/></svg><span class="lbl-t">{label}</span>{extra}</a>')
    return ('<div class="brand"><div class="logo" aria-hidden="true"><svg class="i s16"><use href="#i-bolt"/></svg></div><span>Relume</span></div>'
            '<div class="nav-h">Library</div><nav class="nav" aria-label="Pages">' + ''.join(a) + '</nav>'
            '<div class="foot"><i class="dot" aria-hidden="true"></i><span>Server online · next check 14:00</span></div>')

def sub(text, tag, body):
    return re.sub(rf'<!--{tag}-->.*?<!--/{tag}-->', lambda m: f'<!--{tag}-->{body}<!--/{tag}-->', text, flags=re.S)

for f in root.rglob('*.html'):
    t = f.read_text()
    t = sub(t, 'ICONS', icons)
    t = sub(t, 'CSS', '<style>' + (and_css if 'android' in f.parts else web_css) + '</style>')
    m = re.search(r'<!--NAV:([a-z-]+)-->', t)
    if m:
        t = sub(t, f'NAV:{m.group(1)}', nav(m.group(1)))
    f.write_text(t)
    print('ok', f.relative_to(root))
