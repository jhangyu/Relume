import pathlib, shutil, tempfile, unittest
from unittest import mock
from webui import apklocate, db, jobs, server

FIX = pathlib.Path(__file__).with_name('fixtures') / 'apkmirror'
YT = 'com.google.android.youtube'


def page(name):
    return (FIX / name).read_text()


class FakeEvent:
    def __init__(self, cancelled=False):
        self.waits, self.cancelled = [], cancelled

    def wait(self, seconds):
        self.waits.append(seconds)
        return self.cancelled


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LocateCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.enterContext(mock.patch.object(apklocate, '_clock', self.clock))
        self.enterContext(mock.patch.dict(apklocate._state, {'last': None, 'blocked_until': 0.0, 'retry_at': None}))


class ParseTest(LocateCase):
    def test_parse_search_candidates(self):
        cands = apklocate.parse_search(page('search-youtube.html'))
        self.assertEqual([(c.org, c.slug) for c in cands[:3]],
                         [('yuliskov', 'youtube-for-android-tv'), ('ogmods', 'ogyoutube-3'), ('google-inc', 'youtube')])
        self.assertEqual(len(cands), 10)   # the sidebar release link /apk/<org>/<slug>/<release>/ is not a candidate
        self.assertEqual(cands[2].title, 'YouTube')
        self.assertEqual(cands[9].title, 'YouTube TV: Live TV & more')
        self.assertEqual(apklocate.parse_search(page('search-youtube.html') * 2), cands)   # de-duplicated
        self.assertEqual(apklocate.parse_search(page('search-empty.html')), [])
        with self.assertRaises(apklocate.PageShape):
            apklocate.parse_search(page('challenge.html'))

    def test_play_id_and_missing_block(self):
        self.assertEqual(apklocate.play_id(page('app-youtube.html')), YT)
        self.assertEqual(apklocate.play_id(page('app-ogyoutube.html')), YT)   # a mod points at the same Play id
        with self.assertRaises(apklocate.PageShape):   # the ML Manager link elsewhere on the page does not count
            apklocate.play_id(page('search-youtube.html'))


class DecideTest(LocateCase):
    def fetcher(self, pages):
        calls = []

        def fetch(url):
            calls.append(url)
            return pages.get(url) or page('app-youtube.html').replace(YT, 'com.other.app')
        return fetch, calls

    def test_decide_found_ambiguous_not_found_blocked(self):
        search, yt, og = apklocate.search_url(YT), apklocate.BASE + '/apk/google-inc/youtube/', \
            apklocate.BASE + '/apk/ogmods/ogyoutube-3/'
        pages = {search: page('search-youtube.html'), yt: page('app-youtube.html'), og: page('app-ogyoutube.html')}
        fetch, calls = self.fetcher(pages)
        r = apklocate.decide(YT, 'YouTube', fetch)
        self.assertEqual((r.state, r.list_url), ('ambiguous', None))
        self.assertEqual([c.slug for c in r.candidates], ['youtube', 'ogyoutube-3'])
        self.assertEqual(calls[:2], [search, yt])   # exact store name ranks first
        self.assertEqual(len(calls), 1 + apklocate.MAX_CANDIDATES)

        fetch, _ = self.fetcher({**pages, og: page('app-ogyoutube.html').replace(YT, 'com.mod')})
        r = apklocate.decide(YT, 'YouTube', fetch)
        self.assertEqual((r.state, r.list_url, r.candidates, r.error),
                         ('found', 'https://www.apkmirror.com/uploads/?appcategory=youtube', [], None))

        fetch, calls = self.fetcher({apklocate.search_url('com.example.nothing'): page('search-empty.html')})
        r = apklocate.decide('com.example.nothing', 'Nothing', fetch)
        self.assertEqual((r.state, len(calls)), ('not_found', 1))

        fetch, _ = self.fetcher({**pages, yt: '<html><div class="appRow"></div></html>'})
        r = apklocate.decide(YT, 'YouTube', fetch)
        self.assertEqual(r.state, 'failed')
        self.assertIn(yt, r.error)

        def blocked(url):
            raise apklocate.Blocked('runner exit 1')
        self.assertEqual(apklocate.decide(YT, 'YouTube', blocked).state, 'blocked')

    def test_fetch_challenge_and_runner_failure_start_cooldown(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        (pathlib.Path(tmp.name) / 'work').mkdir()
        self.enterContext(mock.patch.object(db, 'DATA', pathlib.Path(tmp.name)))
        argv = []

        def runner(log, args, home, **kw):
            argv.append((args, kw['env_keys']))
            shutil.copy(FIX / body, pathlib.Path(home) / apklocate.PAGE_FILE)
            return rc
        self.enterContext(mock.patch.object(jobs, 'run_isolated', runner))
        body, rc = 'app-youtube.html', 0
        self.assertEqual(apklocate.play_id(apklocate._fetch('https://u/1', None, FakeEvent())), YT)
        self.assertEqual(argv[0], (['bash', apklocate.FETCH_PAGE, 'https://u/1', apklocate.PAGE_FILE], ('FLARESOLVERR_URL', 'CFBYPASS_URL', 'APKM_SOLVER_URL')))
        self.clock.t += apklocate.MIN_INTERVAL
        body = 'challenge.html'
        with self.assertRaisesRegex(apklocate.Blocked, 'challenge'):
            apklocate._fetch('https://u/2', None, FakeEvent())
        self.assertEqual(apklocate._state['blocked_until'], self.clock.t + apklocate.BLOCK_COOLDOWN)
        self.clock.t += apklocate.BLOCK_COOLDOWN
        body, rc = 'app-youtube.html', 1
        with self.assertRaisesRegex(apklocate.Blocked, 'runner exit 1'):
            apklocate._fetch('https://u/3', None, FakeEvent())
        self.assertEqual(list((pathlib.Path(tmp.name) / 'work').iterdir()), [])   # work dirs removed


class ThrottleTest(LocateCase):
    def test_throttle_waits_and_cooldown_blocks(self):
        ev = FakeEvent()
        apklocate.throttle(ev)
        self.clock.t += 3
        apklocate.throttle(ev)
        self.assertEqual(ev.waits, [apklocate.MIN_INTERVAL - 3])
        self.clock.t += 1
        with self.assertRaisesRegex(jobs.JobFailed, 'cancelled'):
            apklocate.throttle(FakeEvent(cancelled=True))   # the wait returns early on cancel
        apklocate._block('test')
        fetched = []

        def fetch(url):
            apklocate.throttle(ev)
            fetched.append(url)
            return ''
        r = apklocate.decide(YT, 'YouTube', fetch)
        self.assertEqual((r.state, fetched), ('blocked', []))   # zero requests during the cooldown
        self.clock.t += apklocate.BLOCK_COOLDOWN
        apklocate.throttle(ev)   # cooldown over


class UrlTest(unittest.TestCase):
    def test_validate_list_url(self):
        want = 'https://www.apkmirror.com/uploads/?appcategory=youtube'
        self.assertEqual(apklocate.validate_list_url(want), want)
        self.assertEqual(apklocate.validate_list_url('https://apkmirror.com/apk/google-inc/youtube/'), want)
        for bad in ('https://apkpure.com/x', 'http://www.apkmirror.com/uploads/?appcategory=youtube',
                    'https://www.apkmirror.com/apk/google-inc/youtube/youtube-1-release/',
                    'https://www.apkmirror.com/uploads/?appcategory=youtube&page=2', 'https://evil.example/apk/a/b/', 5):
            with self.assertRaises(server.HttpError) as e:
                apklocate.validate_list_url(bad)
            self.assertEqual(e.exception.status, 400)
            self.assertIn('expected https://www.apkmirror.com/uploads/?appcategory=<slug>', e.exception.message)
            self.assertIn(f'got {bad!r}', e.exception.message)


if __name__ == '__main__':
    unittest.main()
