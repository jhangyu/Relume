import contextlib
import io
import json
import pathlib
import unittest
from unittest import mock

from webui import catalog, ci, db, jobs, upstream
from webui.tests import helpers

FIX = pathlib.Path(__file__).parent / 'fixtures' / 'ci'
V = lambda *names: json.dumps([{'name': n, 'abi': n, 'download': {}, 'exclude_splits': None, 'strip_libs': None}
                               for n in names])


def seed():
    with db.tx() as c:
        for uid, kind, repo, tag in ((1, 'patches', 'MorpheApp/morphe-patches', 'v1.21.1'),
                                     (2, 'patches', 'crimera/piko', 'v3.0.0'),
                                     (3, 'cli', 'MorpheApp/morphe-desktop', 'v1.18.1')):
            c.execute('INSERT INTO upstream(id,kind,repo,channel,tag,asset_regex,latest_tag,downloaded_tag) '
                      "VALUES (?,?,?,'stable',NULL,'x',?,?)", (uid, kind, repo, tag, tag))
        for sid, uid in (('morphe', 1), ('piko', 2)):
            c.execute("INSERT INTO source(id,name,upstream_id,cli_upstream_id,engine,keystore) "
                      "VALUES (?,?,?,3,'morphe','morphe.keystore')", (sid, sid, uid))
        for pkg in ('com.google.android.youtube', 'com.twitter.android', 'com.reddit.frontpage'):
            c.execute('INSERT INTO app(package,display_name) VALUES (?,?)', (pkg, pkg))
        for iid, sid, pkg, key, variants, enabled in (
                (1, 'morphe', 'com.google.android.youtube', 'youtube', V('universal', 'arm64-v8a'), 1),
                (2, 'piko', 'com.twitter.android', 'twitter', V('universal'), 1),
                (3, 'morphe', 'com.reddit.frontpage', 'reddit', V('universal'), 0)):
            c.execute('INSERT INTO item(id,source_id,package,key,display_name,group_id,position,enabled,version_mode,'
                      "version_pin,allow_nearest,variants,patch_overrides,engine_options) "
                      "VALUES (?,?,?,?,NULL,NULL,?,?,'auto',NULL,0,?,'{}',NULL)", (iid, sid, pkg, key, iid, enabled, variants))


def release():
    return ci._assets(json.loads((FIX / 'release-all.json').read_text()))


class FetchReleaseTest(unittest.TestCase):
    def test_token_header_and_status_mapping(self):
        sent, replies = [], [(200, {}, b'{"assets": [{"name": "a.apk", "label": "L"}]}'), (200, {}, b'{}'),
                             (404, {}, b''), (500, {}, b'')]

        def fake_get(url, headers=None, timeout=30):
            sent.append((url, headers))
            return replies.pop(0)
        with mock.patch.object(upstream, 'http_get', side_effect=fake_get):
            self.assertEqual(ci.fetch_release('o/r', 'tok'), {'a.apk': 'L'})
            self.assertEqual(ci.fetch_release('o/r', ''), {})
            self.assertEqual(ci.fetch_release('o/r', 'tok'), {})
            with self.assertRaisesRegex(ci.CiFatal, 'GitHub 500 on release "all" of o/r'):
                ci.fetch_release('o/r', 'tok')
        self.assertEqual(sent[0][0], 'https://api.github.com/repos/o/r/releases/tags/all')
        self.assertEqual(sent[0][1].get('Authorization'), 'Bearer tok')
        self.assertNotIn('Authorization', sent[1][1])


class CiPlanTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        seed()
        self.check_errors = {}

        def fake_check(uid):
            with db.tx() as c:
                c.execute('UPDATE upstream SET check_error=? WHERE id=?', (self.check_errors.get(uid), uid))
        patches = [mock.patch.object(upstream, 'check', side_effect=fake_check),
                   mock.patch.object(ci, 'fetch_release', side_effect=lambda repo, token: release()),
                   mock.patch.dict('os.environ', {'GITHUB_REPOSITORY': 'jhangyu/Relume'})]
        started = [self.enterContext(p) for p in patches]
        self.fetch = started[1]

    def main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ci.main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_label_round_trip(self):
        lbl = ci.label('morphe-youtube-universal.apk', '19.47.53', 'v1.21.1')
        self.assertEqual(lbl, 'morphe-youtube-universal.apk (19.47.53, patches v1.21.1)')
        self.assertEqual(ci.label_tag(lbl), 'v1.21.1')

    def test_label_tag_none(self):
        self.assertIsNone(ci.label_tag(None))
        self.assertIsNone(ci.label_tag('youtube-morphe.apk'))

    def test_assets_from_release_json(self):
        a = release()
        self.assertEqual(len(a), 4)
        self.assertIsNone(a['youtube-morphe.apk'])

    def test_plan_label_tag_differs_builds(self):
        self.assertEqual(ci.plan(), ['piko'])

    def test_plan_all_equal_skips(self):
        self.assertNotIn('morphe', ci.plan())

    def test_plan_asset_missing_builds(self):
        a = release()
        del a['morphe-youtube-arm64-v8a.apk']
        self.fetch.side_effect = lambda repo, token: a
        self.assertEqual(ci.plan(), ['morphe', 'piko'])

    def test_plan_check_error_skips(self):
        self.check_errors = {2: 'not found'}
        rc, out, err = self.main('ci-plan')
        self.assertEqual((rc, out), (0, '[]\n'))
        self.assertIn('warning: piko: not found', err)

    def test_plan_force_builds(self):
        self.assertEqual(ci.plan(force=True), ['morphe', 'piko'])

    def test_plan_source_filter(self):
        self.assertEqual(ci.plan('morphe', True), ['morphe'])
        self.assertEqual(ci.plan('morphe', False), [])

    def test_plan_unknown_source_fatal(self):
        rc, out, err = self.main('ci-plan', '--source', 'nope')
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('unknown source nope', err)

    def test_plan_bad_source_id_rejected(self):
        rc, out, err = self.main('ci-plan', '--source', '../x')
        self.assertEqual((rc, out), (1, ''))

    def test_plan_stdout_one_json_line(self):
        rc, out, err = self.main('ci-plan', '--source', 'all', '--force')
        self.assertEqual(rc, 0)
        self.assertEqual(out.count('\n'), 1)
        self.assertEqual(json.loads(out), ['morphe', 'piko'])

    def test_plan_release_403_fatal(self):
        self.fetch.side_effect = ci.CiFatal('GitHub 403 on release "all"')
        rc, out, err = self.main('ci-plan')
        self.assertEqual((rc, out), (1, ''))
        self.assertIn('403', err)

    def test_plan_every_upstream_rate_limited_fatal(self):
        self.check_errors = {1: 'rate limited until 2026-10-19T10:00:00Z', 2: 'token rejected'}
        rc, out, err = self.main('ci-plan')
        self.assertEqual((rc, out), (1, ''))

    def test_export_seed_prints_seed_text(self):
        with mock.patch.object(catalog, 'seed_text', return_value='{"sources": []}\n'):
            rc, out, err = self.main('export-seed')
        self.assertEqual((rc, out), (0, '{"sources": []}\n'))


class CiBuildTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        seed()
        with db.tx() as c:  # second enabled morphe item (reddit) that will fail
            c.execute("UPDATE item SET enabled=1, variants=? WHERE id=3", (V('universal'),))
            c.execute("UPDATE item SET variants=? WHERE id=1", (V('universal'),))
        self.calls, self.fail_items, self.swap = [], {3}, None

        def fake_run_inline(*args, **kwargs):
            self.calls.append((args, kwargs))
            kind, target, trigger = args
            status = 'failed' if target.get('item_id') in self.fail_items else 'success'
            with db.tx() as c:
                jid = c.execute('INSERT INTO jobs(kind,status,trigger,target,options,created_at) VALUES (?,?,?,?,?,?)',
                                (kind, status, trigger, json.dumps(target), '{}', db.utcnow())).lastrowid
                if kind == 'build':
                    iid = target['item_id']
                    c.execute('INSERT INTO build_jobs(job_id,item_id,app_version,patches_version,cli_version) '
                              "VALUES (?,?,'19.47.53','v1.21.1','v1.18.1')", (jid, iid))
                    if status == 'success':
                        f = db.DATA / f'art-{jid}.apk'
                        f.write_bytes(b'APK')
                        if self.swap:
                            f.unlink()
                            f.symlink_to(self.swap)
                        c.execute('INSERT INTO artifacts(job_id,item_id,variant,abi,package,version_name,version_code,'
                                  'patches_version,signer_sha256,path,size,sha256,created_at) '
                                  "VALUES (?,?,'universal','universal','p','19.47.53',1,'v1.21.1','s',?,3,'h',?)",
                                  (jid, iid, str(f), db.utcnow()))
            (db.DATA / 'logs').mkdir(exist_ok=True)
            (db.DATA / 'logs' / f'{jid}.log').write_text(f'10:00:00 log of job {jid}\n')
            return {'id': jid, 'status': status}

        patches = [mock.patch.object(jobs, 'run_inline', side_effect=fake_run_inline),
                   mock.patch.object(ci, 'fetch_release', side_effect=lambda repo, token: {}),
                   mock.patch.dict('os.environ', {'GITHUB_REPOSITORY': 'o/r'})]
        started = [self.enterContext(p) for p in patches]
        self.fetch = started[1]

    def build(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = ci.main(['ci-build', *argv])
        return rc, out.getvalue()

    def test_build_publishes_success_only(self):
        rc, out = self.build('morphe')
        self.assertEqual(rc, 1)
        rel = db.DATA / 'release'
        self.assertEqual(sorted(p.name for p in rel.iterdir()), ['morphe-youtube-universal.apk', 'upload.list'])
        name = 'morphe-youtube-universal.apk'
        self.assertEqual((rel / 'upload.list').read_text(), f"{name}#{ci.label(name, '19.47.53', 'v1.21.1')}\n")
        self.assertEqual((rel / name).read_bytes(), b'APK')

    def test_build_passes_no_options(self):
        self.build('morphe', '--force')
        self.assertTrue(self.calls)
        for args, kwargs in self.calls:
            self.assertEqual((len(args), kwargs), (3, {}))

    def test_build_pulls_source_and_cli_upstream(self):
        self.build('morphe', '--force')
        self.assertEqual([a for a, k in self.calls[:2]],
                         [('upstream', {'upstream_id': 1}, 'manual'), ('upstream', {'upstream_id': 3}, 'manual')])
        self.assertEqual([a[1] for a, k in self.calls[2:]], [{'item_id': 1}, {'item_id': 3}])

    def test_build_force_skips_release_fetch(self):
        self.fail_items = set()
        rc, out = self.build('morphe', '--force')
        self.assertEqual(rc, 0)
        self.fetch.assert_not_called()
        self.build('morphe')
        self.fetch.assert_called_once()

    def test_build_prints_job_log(self):
        rc, out = self.build('morphe', '--force')
        self.assertIn('log of job 3', out)
        self.assertIn('log of job 4', out)

    def test_build_unknown_source(self):
        self.assertEqual(self.build('nope')[0], 1)

    def test_build_symlink_artifact_not_published(self):
        secret = db.DATA / 'github_token'
        secret.write_text('TOKEN')
        self.swap = secret
        rc, out = self.build('morphe', '--force')
        self.assertEqual(rc, 1)
        rel = db.DATA / 'release'
        self.assertFalse(any(b'TOKEN' in p.read_bytes() for p in rel.iterdir() if p.name != 'upload.list'))
        self.assertNotIn('apk#', (rel / 'upload.list').read_text() if (rel / 'upload.list').exists() else '')
