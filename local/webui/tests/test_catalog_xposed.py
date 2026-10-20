import json

from webui import catalog, db
from webui.tests import helpers

XV = {'name': 'arm64-v8a', 'abi': 'arm64-v8a', 'exclude_splits': None, 'strip_libs': None,
      'download': {'kind': 'apkmirror', 'type': 'bundle', 'arch': 'arm64-v8a', 'dpi': '320-640dpi',
                   'minver': 'Android 11+', 'url': None}}


class XposedCatalogTest(helpers.DataTestCase):
    def setUp(self):
        super().setUp()
        with db.tx() as c:
            for uid, kind, repo, tag in ((1, 'patches', 'MorpheApp/morphe-patches', 'v1.21.1'),
                                         (2, 'cli', 'MorpheApp/morphe-desktop', 'v1.18.1'),
                                         (3, 'module', 'gnadgnaoh/NexAlloy', 'v1.1'),
                                         (4, 'cli', 'JingMatrix/LSPatch', 'v1.2'),
                                         (5, 'cli', '7723mod/NPatch', 'v1.0.5')):
                c.execute('INSERT INTO upstream(id,kind,repo,channel,tag,asset_regex,latest_tag,downloaded_tag) '
                          "VALUES (?,?,?,'stable',NULL,'.*',?,?)", (uid, kind, repo, tag, tag))
            for sid, uid, cli, engine, ks in (('morphe', 1, 2, 'morphe', 'morphe.keystore'),
                                              ('nexalloy-lspatch', 3, 4, 'lspatch', 'lspatch-signing.p12'),
                                              ('nexalloy-npatch', 3, 5, 'npatch', 'fiorenmas.ks')):
                c.execute('INSERT INTO source(id,name,upstream_id,cli_upstream_id,engine,keystore) VALUES (?,?,?,?,?,?)',
                          (sid, sid, uid, cli, engine, ks))
            for pkg in ('com.facebook.katana', 'com.zhiliaoapp.musically', 'com.google.android.youtube'):
                c.execute('INSERT INTO app(package,display_name) VALUES (?,?)', (pkg, pkg))
            for iid, sid, pkg, key, eo, variants in (
                    (1, 'nexalloy-lspatch', 'com.facebook.katana', 'facebook-arm64-v8a',
                     '{"sigbypass":3,"injectdex":true}', json.dumps([XV])),
                    (2, 'morphe', 'com.google.android.youtube', 'youtube', None, '[]')):
                c.execute('INSERT INTO item(id,source_id,package,key,display_name,group_id,position,enabled,version_mode,'
                          "version_pin,allow_nearest,variants,patch_overrides,engine_options) "
                          "VALUES (?,?,?,?,NULL,NULL,?,1,'latest',NULL,0,?,'{}',?)", (iid, sid, pkg, key, iid, variants, eo))
        self.base = helpers.serve(self, ['catalog'])
        self.cookie = helpers.login(self.base)

    def call(self, method, path, body=None):
        st, _, raw = helpers.http(method, self.base + path, body, self.cookie)
        return st, json.loads(raw) if raw else None

    def test_auto_rejected(self):
        st, body = self.call('PUT', '/api/items/1/version', {'version_mode': 'auto', 'version_pin': None, 'allow_nearest': False})
        self.assertEqual((st, body), (400, {'error': 'auto version not available for module items'}))
        st, _ = self.call('PUT', '/api/items/1/version', {'version_mode': 'latest', 'version_pin': None, 'allow_nearest': False})
        self.assertLess(st, 300)

    def test_sigbypass_4_lspatch_rejected_npatch_ok(self):
        base = {'package': 'com.zhiliaoapp.musically', 'group_id': None, 'variants': [dict(XV, name='universal', abi='universal')],
                'engine_options': {'sigbypass': 4, 'injectdex': False}}
        st, body = self.call('POST', '/api/items', dict(base, source_id='nexalloy-lspatch'))
        self.assertEqual((st, body), (400, {'error': 'sigbypass must be 0-3 for lspatch'}))
        st, _ = self.call('POST', '/api/items', dict(base, source_id='nexalloy-npatch'))
        self.assertLess(st, 300)

    def test_engine_options_shape(self):
        for eo in (None, {'sigbypass': 1}, {'sigbypass': 1, 'injectdex': False, 'x': 1}, {'sigbypass': True, 'injectdex': False},
                   {'sigbypass': 1, 'injectdex': 'yes'}):
            st, _ = self.call('POST', '/api/items', {'source_id': 'nexalloy-npatch', 'package': 'com.zhiliaoapp.musically',
                                                     'group_id': None, 'variants': [XV], 'engine_options': eo})
            self.assertEqual(st, 400, eo)

    def test_strip_libs_rejected(self):
        st, body = self.call('PUT', '/api/items/1', {'variants': [dict(XV, strip_libs='arm64-v8a')]})
        self.assertEqual((st, body), (400, {'error': 'exclude_splits and strip_libs must be null for module items'}))

    def test_bundle_extract_rejected(self):
        v = dict(XV, download=dict(XV['download'], type='bundle_extract'))
        st, body = self.call('PUT', '/api/items/1', {'variants': [v]})
        self.assertEqual((st, body), (400, {'error': 'download.type must be apk or bundle for module items'}))

    def test_morphe_item_engine_options_must_be_null(self):
        st, body = self.call('PUT', '/api/items/2', {'engine_options': {'sigbypass': 0, 'injectdex': False}})
        self.assertEqual((st, body), (400, {'error': 'engine_options must be null for patches items'}))

    def test_patches_routes_409(self):
        self.assertEqual(self.call('GET', '/api/items/1/patches'), (409, {'error': 'not a patches item'}))
        self.assertEqual(self.call('PUT', '/api/items/1/patches', {'overrides': {}}), (409, {'error': 'not a patches item'}))

    def test_module_source_needs_asset_regex(self):
        body = {'id': 'revenge-lspatch', 'name': 'Revenge', 'engine': 'lspatch', 'repo': 'revenge-mod/revenge-xposed',
                'channel': 'stable', 'keystore': 'lspatch-signing.p12', 'cli_upstream_id': 4}
        st, err = self.call('POST', '/api/sources', body)
        self.assertEqual((st, err), (400, {'error': 'asset_regex required for module sources'}))
        st, _ = self.call('POST', '/api/sources', dict(body, asset_regex='^app-release\\.apk$'))
        self.assertLess(st, 300)
        kind = db.conn().execute("SELECT u.kind FROM source s JOIN upstream u ON u.id = s.upstream_id "
                                 "WHERE s.id = 'revenge-lspatch'").fetchone()['kind']
        self.assertEqual(kind, 'module')

    def test_job_input_xposed(self):
        mod = db.DATA / 'upstream' / '3' / 'v1.1'
        jar = db.DATA / 'upstream' / '4' / 'v1.2'
        mod.mkdir(parents=True)
        jar.mkdir(parents=True)
        (mod / 'NexAlloy-refactor-release-v1.1.apk').write_bytes(b'MOD')
        (jar / 'lspatch-v1.2-487-release.jar').write_bytes(b'JAR')
        j = catalog.job_input(1)
        self.assertEqual(j['engine'], 'lspatch')
        self.assertEqual(j['engine_options'], {'sigbypass': 3, 'injectdex': True})
        self.assertEqual(j['patches_files'], [str(mod / 'NexAlloy-refactor-release-v1.1.apk')])
        self.assertEqual(j['cli_jar'], str(jar / 'lspatch-v1.2-487-release.jar'))
        self.assertIsNone(j['options_file_json'])
        self.assertEqual((j['upstream_tag'], j['cli_tag'], j['keystore']), ('v1.1', 'v1.2', 'lspatch-signing.p12'))

    def test_arch_leading_dash_rejected(self):
        for arch in ('-v', '-fx', '.x', ' x'):
            v = dict(XV, download=dict(XV['download'], arch=arch))
            st, body = self.call('PUT', '/api/items/1', {'variants': [v]})
            self.assertEqual((st, body), (400, {'error': 'invalid arch'}), arch)
        v = dict(XV, download=dict(XV['download'], arch='arm64-v8a + armeabi-v7a'))
        st, _ = self.call('PUT', '/api/items/1', {'variants': [v]})
        self.assertLess(st, 300)
