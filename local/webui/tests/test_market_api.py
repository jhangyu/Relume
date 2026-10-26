import json, unittest
from unittest import mock
from webui import apklocate, apps, auth, catalog, db, server
from webui.tests import catalog_rows
from webui.tests.helpers import DataTestCase, serve, http, login

FEED = {'feeds': [], 'sources': []}


def fake(mod, name, **kw):
    """patch.object; create=True while task 1.3 has not committed the catalog read functions yet."""
    return mock.patch.object(mod, name, create=True, **kw)


def market_app(package, name, category, origins, bundles, stars, updated):
    return {'package': package, 'name': name, 'category': category, 'description': f'{name} app', 'origins': origins,
            'bundle_count': bundles, 'stars': stars, 'newest_version': None, 'updated_at': updated}


APPS = [market_app('com.b.reddit', 'Reddit', 'Social', ['builtin', 'official'], 4, 50, '2026-10-01T00:00:00Z'),
        market_app('com.a.youtube', 'YouTube', 'Video', ['builtin'], 2, 900, '2026-10-20T00:00:00Z'),
        market_app('com.c.tiktok', 'TikTok', 'Social', ['official'], 7, None, None),
        market_app('com.d.custom', 'Custom App', None, ['custom'], 1, 10, '2026-10-25T00:00:00Z'),
        market_app('com.e.music', 'Music', 'Video', ['official'], 3, 300, '2026-09-01T00:00:00Z')]


class MarketCase(DataTestCase):
    def setUp(self):
        super().setUp()
        self.enterContext(mock.patch.object(auth, 'SCRYPT_N', 2 ** 4))   # fast hashing, the KDF cost is not under test
        self.enterContext(fake(catalog, 'feed_status', return_value=FEED))
        self.base = serve(self, names=('market',))
        self.cookie = login(self.base)

    def get(self, path, **kw):
        st, _, body = http('GET', self.base + path, cookie=self.cookie, **kw)
        return st, json.loads(body)

    def send(self, method, path, body=None):
        st, _, raw = http(method, self.base + path, body if body is not None else {}, cookie=self.cookie)
        return st, json.loads(raw) if raw else None


class MarketplaceTest(MarketCase):
    def setUp(self):
        super().setUp()
        self.enterContext(fake(catalog, 'market_apps', return_value=APPS))
        with db.tx() as c:
            b = catalog_rows.builtin_id(c)
            self.yt = catalog_rows.add_item(c, 'com.a.youtube', b)
            self.rd = catalog_rows.add_item(c, 'com.b.reddit', b, enabled=0)

    def pkgs(self, query):
        st, body = self.get('/api/marketplace?' + query)
        self.assertEqual(st, 200, body)
        return [a['package'] for a in body['apps']], body

    def test_marketplace_filter_sort_page(self):
        names, body = self.pkgs('')
        self.assertEqual(names, ['com.d.custom', 'com.e.music', 'com.b.reddit', 'com.c.tiktok', 'com.a.youtube'])
        self.assertEqual((body['total'], body['page'], body['per_page'], body['feed']), (5, 1, 50, FEED))
        self.assertEqual(body['categories'], [{'name': 'Social', 'count': 2}, {'name': 'Video', 'count': 2}])
        yt = body['apps'][4]
        self.assertEqual((yt['app_id'], yt['enabled'], yt['icon_url'], yt['stars']),
                         (self.yt, True, '/api/apps/com.a.youtube/icon', 900))
        self.assertEqual([(a['app_id'], a['enabled']) for a in body['apps'][1:3]], [(None, False), (self.rd, False)])
        self.assertEqual(self.pkgs('sort=bundles')[0][:2], ['com.c.tiktok', 'com.b.reddit'])
        self.assertEqual(self.pkgs('sort=stars')[0], ['com.a.youtube', 'com.e.music', 'com.b.reddit', 'com.d.custom',
                                                      'com.c.tiktok'])
        self.assertEqual(self.pkgs('sort=updated')[0][:2], ['com.d.custom', 'com.a.youtube'])
        self.assertEqual(self.pkgs('sort=updated')[0][-1], 'com.c.tiktok')   # no date sorts last
        self.assertEqual(self.pkgs('q=TUBE')[0], ['com.a.youtube'])
        self.assertEqual(self.pkgs('q=com.c')[0], ['com.c.tiktok'])
        self.assertEqual(self.pkgs('origin=builtin')[0], ['com.b.reddit', 'com.a.youtube'])
        self.assertEqual(self.pkgs('enabled=true')[0], ['com.a.youtube'])
        self.assertEqual(len(self.pkgs('enabled=false')[0]), 4)   # a disabled row counts as not enabled
        names, body = self.pkgs('category=Social&origin=official')
        self.assertEqual((names, body['categories']),   # categories ignore the category filter only
                         (['com.b.reddit', 'com.c.tiktok'], [{'name': 'Social', 'count': 2}, {'name': 'Video', 'count': 1}]))
        names, body = self.pkgs('per_page=2&page=3')
        self.assertEqual((names, body['total']), (['com.a.youtube'], 5))
        self.assertEqual(self.pkgs('per_page=2&page=9')[0], [])

    def test_marketplace_bad_per_page_is_400(self):
        st, body = self.get('/api/marketplace?per_page=500')
        self.assertEqual((st, body), (400, {'error': "marketplace: per_page expected 1-100, got '500'"}))
        for q in ('per_page=0', 'per_page=x', 'page=0', 'sort=rating', 'origin=gitlab', 'enabled=yes'):
            st, body = self.get('/api/marketplace?' + q)
            self.assertEqual(st, 400, q)
            self.assertIn('expected', body['error'], q)


class AppsTest(MarketCase):
    def setUp(self):
        super().setUp()
        with db.tx() as c:
            self.basic = catalog_rows.add_basic(c)
        self.enabled = []

        def enable(package, bundle_id, overrides=None):
            if bundle_id == 99:
                raise server.HttpError(409, f'bundle 99 (github o/r): does not support package {package}',
                                       'bundle_unsupported')
            self.enabled.append((package, bundle_id, overrides))
            return {'id': 4, 'package': package}
        self.enterContext(mock.patch.object(apps, 'enable', side_effect=enable))

    def test_enable_201_and_conflicts(self):
        st, body = self.send('POST', '/api/apps', {
            'package': 'com.x.app', 'bundle_id': self.basic['official'], 'patches': {'Hide ads': False},
            'download': {'apkmirror_list_url': 'https://www.apkmirror.com/apk/x-inc/x-app/'}})
        self.assertEqual(st, 201, body)
        self.assertEqual(self.enabled, [('com.x.app', self.basic['official'], {'Hide ads': False})])
        self.assertEqual((body['id'], body['download']['state'], body['download']['apkmirror_list_url']),
                         (4, 'manual', 'https://www.apkmirror.com/uploads/?appcategory=x-app'))
        st, body = self.send('POST', '/api/apps', {'package': 'com.x.app', 'bundle_id': 99})
        self.assertEqual((st, body['code']), (409, 'bundle_unsupported'))
        self.assertIn('does not support package com.x.app', body['error'])
        for bad in ({'package': 'com.y.app', 'bundle_id': 1, 'download': {'apkmirror_list_url': 'https://apkpure.com/y'}},
                    {'package': 'com.y.app', 'bundle_id': '1'}, {'package': 'com.y.app', 'bundle_id': 1, 'patches': {'a': 1}},
                    {'package': 'not a package', 'bundle_id': 1}, {'package': 'com.y.app', 'bundle_id': 1, 'x': 1}):
            st, body = self.send('POST', '/api/apps', bad)
            self.assertEqual(st, 400, bad)
        self.assertEqual(len(self.enabled), 1)   # nothing reached apps.enable after a 400
        self.assertIsNone(apklocate.download_state('com.y.app'))   # a bad URL writes no row

    def test_build_without_url_is_409(self):
        pkg = 'com.x.nourl'   # the seed rows of 010 give YouTube and Reddit a listing URL already
        with db.tx() as c:
            yt = catalog_rows.add_item(c, pkg, self.basic['builtin'])
        checks, saved = [], []

        def rebuild(app_id):   # stand-in for apps.rebuild's URL guard (task 1.2)
            if not apklocate.download_state(pkg)['apkmirror_list_url']:
                raise server.HttpError(409, f'app {app_id} ({pkg}): build needs an APKMirror listing URL',
                                       'needs_download_url')
            return 55
        self.enterContext(mock.patch.object(apps, 'rebuild', side_effect=rebuild))
        self.enterContext(mock.patch.object(apps, 'get', return_value={'id': yt, 'package': pkg}))
        self.enterContext(mock.patch.object(apps, 'check_update', side_effect=lambda i: checks.append(i) or 9))
        self.enterContext(mock.patch.object(apps, 'update_settings', side_effect=lambda i, f: saved.append(f)))
        st, body = self.send('POST', f'/api/apps/{yt}/build')
        self.assertEqual((st, body['code']), (409, 'needs_download_url'))
        self.assertIn('build needs an APKMirror listing URL', body['error'])
        st, body = self.send('PUT', f'/api/apps/{yt}', {'display_name': 'YT', 'download': {'apkpure_url': 'ftp://x'}})
        self.assertEqual((st, saved), (400, []))   # the download part is validated before the settings are saved
        url = {'download': {'apkmirror_list_url': 'https://www.apkmirror.com/uploads/?appcategory=youtube'}}
        st, body = self.send('PUT', f'/api/apps/{yt}', {'display_name': 'YT', **url})
        self.assertEqual((st, body['download']['state'], saved), (200, 'manual', [{'display_name': 'YT'}]), body)
        self.assertEqual(checks, [yt])   # a newly set URL starts a check
        self.send('PUT', f'/api/apps/{yt}', url)
        self.assertEqual(checks, [yt])   # an unchanged URL does not
        self.assertEqual(self.send('POST', f'/api/apps/{yt}/build'), (200, {'job_id': 55}))


class DeviceTokenTest(MarketCase):
    def test_device_token_reaches_new_routes(self):
        st, _, body = http('POST', self.base + '/api/auth/device', {'username': 'admin', 'password': 'correct-horse-42',
                           'device': {'name': 'Pixel 8', 'model': 'Pixel 8', 'sdk': 35, 'android_release': '15',
                                      'abi_list': ['arm64-v8a']}})
        self.assertEqual(st, 200, body)
        bearer = {'Authorization': f"Bearer {json.loads(body)['token']}"}
        self.enterContext(fake(catalog, 'market_apps', return_value=APPS[:1]))
        self.enterContext(mock.patch.object(apps, 'list_enabled', return_value=[]))
        self.enterContext(fake(catalog, 'add_custom', return_value=7))
        deleted = []
        self.enterContext(fake(catalog, 'delete_custom', side_effect=deleted.append))
        st, _, body = http('GET', self.base + '/api/marketplace', headers=bearer)
        self.assertEqual((st, json.loads(body)['total']), (200, 1))
        self.assertEqual(http('GET', self.base + '/api/apps', headers=bearer)[::2], (200, b'{"apps": []}'))
        st, _, body = http('POST', self.base + '/api/sources', {'url': 'https://github.com/o/r', 'engine': 'morphe'},
                           headers=bearer)
        self.assertEqual((st, json.loads(body)), (201, {'bundle_id': 7}))
        self.assertEqual(http('DELETE', self.base + '/api/sources/7', {}, headers=bearer)[::2], (204, b''))
        self.assertEqual(deleted, [7])
        self.assertEqual(http('GET', self.base + '/api/marketplace')[0], 401)
        st, _, body = http('PUT', self.base + '/api/settings', {}, headers=bearer)
        self.assertEqual((st, json.loads(body)), (403, {'error': 'session required'}))


if __name__ == '__main__':
    unittest.main()
