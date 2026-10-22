"""CI subcommands (spec ci 4). Called by webui.__main__ after boot(): schema + seed import + register() only."""
import argparse
import contextlib
import json
import os
import pathlib
import re
import shutil
import sys
import urllib.error
import urllib.request

from . import catalog, db, jobs, settings, upstream

GITHUB_API = 'https://api.github.com'
_TAG = re.compile(r', patches (\S+)\)$')
_FATAL_CHECK = ('token rejected', 'rate limited')
_SOURCE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
_NAME_PART = re.compile(r'^[A-Za-z0-9_][A-Za-z0-9_.-]*$')  # no '/', no leading '.': safe as a file name part
_REPO = re.compile(r'^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$')


class CiFatal(Exception):
    pass


def label(name, app_version, tag):
    return f'{name} ({app_version}, patches {tag})'


def label_tag(lbl):
    m = _TAG.search(lbl or '')
    return m.group(1) if m else None


def asset_name(source_id, key, variant):
    for part in (source_id, key, variant):
        if not _NAME_PART.match(str(part)):
            raise CiFatal(f'unsafe asset name part {part!r}')
    return f'{source_id}-{key}-{variant}.apk'


def _assets(release):
    return {a['name']: a.get('label') for a in release.get('assets', [])}


def fetch_release(repo, token):
    headers = {'Accept': 'application/vnd.github+json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    req = urllib.request.Request(f'{GITHUB_API}/repos/{repo}/releases/tags/all', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return _assets(json.load(r))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}
        raise CiFatal(f'GitHub {e.code} on release "all" of {repo}')
    except OSError as e:
        raise CiFatal(f'GitHub unreachable: {e}')


def _repo():
    repo = os.environ.get('GITHUB_REPOSITORY', '')
    if not _REPO.match(repo):
        raise CiFatal('GITHUB_REPOSITORY is not set or malformed')
    return repo


def _source_arg(source, allow_all):
    if not (allow_all and source == 'all') and not _SOURCE.match(source):
        raise CiFatal(f'invalid source id {source!r}')


def _sources(source):
    _source_arg(source, True)
    rows = db.conn().execute('SELECT DISTINCT s.id, s.upstream_id, s.cli_upstream_id FROM source s '
                             'JOIN item i ON i.source_id = s.id AND i.enabled = 1 ORDER BY s.id').fetchall()
    if source != 'all':
        rows = [r for r in rows if r['id'] == source]
        if not rows:
            raise CiFatal(f'unknown source {source} (or it has no enabled item)')
    return rows


def stale_items(source_id, assets, latest_tag, force):
    out = []
    for r in db.conn().execute('SELECT id, key, variants FROM item WHERE source_id=? AND enabled=1 ORDER BY id',
                               (source_id,)):
        names = [asset_name(source_id, r['key'], v['name']) for v in json.loads(r['variants'] or '[]')]
        if force or any(n not in assets or label_tag(assets[n]) != latest_tag for n in names):
            out.append(r['id'])
    return out


def plan(source='all', force=False):
    rows = _sources(source)
    uids = sorted({r['upstream_id'] for r in rows})
    for uid in uids:
        upstream.check(uid)
    ups = {u: dict(db.conn().execute('SELECT latest_tag, check_error FROM upstream WHERE id=?', (u,)).fetchone())
           for u in uids}
    if uids and all((ups[u]['check_error'] or '').startswith(_FATAL_CHECK) for u in uids):
        raise CiFatal('github unavailable for every upstream')
    assets = fetch_release(_repo(), settings.github_token())
    out = []
    for r in rows:
        u = ups[r['upstream_id']]
        if u['check_error']:
            print(f"warning: {r['id']}: {u['check_error']}", file=sys.stderr)
        elif force or stale_items(r['id'], assets, u['latest_tag'], False):
            out.append(r['id'])
    return out


def _publish(source_id, item_id, job_id, rel):
    c = db.conn()
    key = c.execute('SELECT key FROM item WHERE id=?', (item_id,)).fetchone()['key']
    bj = c.execute('SELECT app_version, patches_version FROM build_jobs WHERE job_id=?', (job_id,)).fetchone()
    with open(rel / 'upload.list', 'a', encoding='utf-8') as f:
        for a in c.execute('SELECT variant, path FROM artifacts WHERE job_id=? ORDER BY id', (job_id,)):
            name = asset_name(source_id, key, a['variant'])
            src = pathlib.Path(a['path'])
            try:  # no symlink swapped in by a lingering runner child
                with jobs.open_runner_file(src.parent, src.name) as fin, open(rel / name, 'wb') as out:
                    shutil.copyfileobj(fin, out, 1 << 20)
            except OSError as e:
                raise CiFatal(f'artifact {src} rejected: {e}')
            f.write(f"{name}#{label(name, bj['app_version'], bj['patches_version'])}\n")


def build(source_id, force=False):
    """Spec ci 4.2: pull the source and CLI upstreams, build stale items, publish successes, exit 1 on any failure."""
    _source_arg(source_id, False)
    c = db.conn()
    src = c.execute('SELECT id, upstream_id, cli_upstream_id FROM source WHERE id=?', (source_id,)).fetchone()
    if src is None:
        print(f'error: unknown source {source_id}', file=sys.stderr)
        return 1
    jobs.run_inline('upstream', {'upstream_id': src['upstream_id']}, 'manual')
    if src['cli_upstream_id'] and src['cli_upstream_id'] != src['upstream_id']:
        jobs.run_inline('upstream', {'upstream_id': src['cli_upstream_id']}, 'manual')
    latest = c.execute('SELECT latest_tag FROM upstream WHERE id=?', (src['upstream_id'],)).fetchone()['latest_tag']
    assets = {} if force else fetch_release(_repo(), settings.github_token())
    rel = db.DATA / 'release'
    rel.mkdir(parents=True, exist_ok=True)
    ok = True
    for item_id in stale_items(source_id, assets, latest, force):
        row = jobs.run_inline('build', {'item_id': item_id}, 'manual')
        log = db.DATA / 'logs' / f"{row['id']}.log"
        sys.stdout.write(log.read_text(encoding='utf-8', errors='replace') if log.exists() else 'no log\n')
        sys.stdout.flush()
        if row['status'] == 'success':
            try:
                _publish(source_id, item_id, row['id'], rel)
            except CiFatal as e:
                print(f'error: {e}', file=sys.stderr)
                ok = False
        else:
            ok = False
    return 0 if ok else 1


def main(argv):
    p = argparse.ArgumentParser(prog='python3 -m webui')
    sub = p.add_subparsers(dest='cmd', required=True)
    a = sub.add_parser('ci-plan')
    a.add_argument('--source', default='all')
    a.add_argument('--force', action='store_true')
    b = sub.add_parser('ci-build')
    b.add_argument('source')
    b.add_argument('--force', action='store_true')
    sub.add_parser('export-seed')
    args = p.parse_args(argv)
    if args.cmd == 'export-seed':
        sys.stdout.write(catalog.seed_text())
        return 0
    try:
        if args.cmd == 'ci-plan':
            out = sys.stdout
            with contextlib.redirect_stdout(sys.stderr):
                result = plan(args.source, args.force)
            out.write(json.dumps(result) + '\n')
            return 0
        return build(args.source, args.force)
    except CiFatal as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
