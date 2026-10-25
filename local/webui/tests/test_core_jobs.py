import json, os, pathlib, select, sqlite3, subprocess, tempfile, threading, time, unittest
from unittest import mock
from webui import db, jobs, settings
from webui.tests.helpers import DataTestCase


def wait_status(job_id, statuses, timeout=10):
    """Final statuses only: the job writes its status before Log.close() notifies this condition."""
    def reached():
        j = jobs.get(job_id)
        return j if j and j['status'] in statuses else None
    cond = jobs.log_condition(job_id)
    with cond:
        j = cond.wait_for(reached, timeout)
    if not j:
        raise AssertionError(f'job {job_id} not in {statuses} within {timeout}s: {jobs.get(job_id)}')
    return j


def assert_dead(case, pid):
    """Wait for the process to exit through its pidfd, then require /proc to show it gone or a zombie."""
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        state = 'gone'
    else:
        try:
            case.assertTrue(select.select([fd], [], [], 5)[0], f'pid {pid} still alive after 5s')
        finally:
            os.close(fd)
        # ponytail: the orphaned child is reparented to PID 1 (python in the test container, no reaper),
        # so a killed child stays a zombie (state Z); dead either way
        try:
            state = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            state = 'gone'
    case.assertIn(state, ('gone', 'Z'))


WAKE_LIMIT_S = 0.5   # a woken worker claims in milliseconds; a lost wakeup would wait for no notify at all
FIRST_JOB_ID = 1     # every test starts on a fresh database

needs_pidfd = unittest.skipUnless(hasattr(os, 'pidfd_open'), 'needs Linux pidfd')


class EnqueueTest(DataTestCase):
    def test_dedup_same_target(self):
        a = jobs.enqueue('build', {'item_id': 1}, 'schedule', schedule_id=None)
        b = jobs.enqueue('build', {'item_id': 1}, 'manual')
        self.assertEqual(a, b)

    def test_force_reinstall_not_merged(self):
        a = jobs.enqueue('ota', {'device_id': 1}, 'manual')
        b = jobs.enqueue('ota', {'device_id': 1}, 'manual', options={'mode': 'force_reinstall', 'package': 'x.y'})
        self.assertNotEqual(a, b)

    def test_unknown_option_dropped_and_defaults(self):
        jid = jobs.enqueue('upstream', {'upstream_id': 3}, 'manual', options={'rebuild': True, 'bogus': 1})
        self.assertEqual(jobs.get(jid)['options'], {'rebuild': True, 'notify': True})
        raw = db.conn().execute('SELECT options, target FROM jobs WHERE id=?', (jid,)).fetchone()
        self.assertEqual(raw['options'], '{"notify":true,"rebuild":true}')
        self.assertEqual(raw['target'], '{"upstream_id":3}')
        jid = jobs.enqueue('appcheck', {'package': 'x.y'}, 'schedule')
        self.assertEqual(jobs.get(jid)['options'], {'rebuild': False, 'notify': True})



class RunIsolatedTest(unittest.TestCase):
    """Third-party code sees only whitelisted server variables, never GITHUB_TOKEN or the server's HOME."""

    class CaptureLog:
        def run(self, argv, cwd=None, env=None, timeout=None, on_line=None):
            self.argv, self.cwd, self.env = argv, cwd, env
            p = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
            for text in p.stdout.splitlines():
                on_line(text)
            return p.returncode

    ENVIRON = {'GITHUB_TOKEN': 'secret', 'HOME': '/root', 'PATH': os.environ['PATH'], 'FLARESOLVERR_URL': 'http://fs',
               'LANG': 'C.UTF-8'}

    def test_env_whitelist(self):
        log, lines = self.CaptureLog(), []
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, self.ENVIRON, clear=True):
            rc = jobs.run_isolated(log, ['env'], home, env_keys=('FLARESOLVERR_URL',), env={'EXTRA': 'x'},
                                   on_line=lines.append)
            self.assertEqual(rc, 0)
            self.assertEqual(log.cwd, home)
            self.assertEqual(sorted(lines), sorted([f'PATH={os.environ["PATH"]}', 'LANG=C.UTF-8',
                                                    'FLARESOLVERR_URL=http://fs', 'EXTRA=x', f'HOME={home}']))
        log, lines = self.CaptureLog(), []
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, self.ENVIRON, clear=True):
            jobs.run_isolated(log, ['env'], home, env={'HOME': '/root'}, on_line=lines.append)
            self.assertEqual(sorted(lines), sorted([f'PATH={os.environ["PATH"]}', 'LANG=C.UTF-8', f'HOME={home}']))

    def test_runner_uid_uses_setpriv(self):
        log = mock.Mock()
        with mock.patch.dict(os.environ, {'RELUME_RUNNER_UID': '10001'}):
            jobs.run_isolated(log, ['true'], '/tmp')
        argv = log.run.call_args.args[0]
        self.assertEqual(argv[:2], ['setpriv', '--reuid=10001'])
        self.assertIn('--no-new-privs', argv)
        self.assertEqual(argv[-1], 'true')


class WorkerTest(DataTestCase):
    def setUp(self):
        super().setUp()
        jobs.start()

    def test_serial_per_kind_parallel_across(self):
        running, peak, lock = [], {'build': 0, 'ota': 0}, threading.Lock()
        started = {'build': threading.Event(), 'ota': threading.Event()}
        hold, entered = threading.Event(), []

        def h(job, log):
            with lock:
                running.append(job.kind)
                entered.append(job.id)
                peak[job.kind] = max(peak[job.kind], running.count(job.kind))
                both = 'build' in running and 'ota' in running
            started[job.kind].set()
            other = started['ota' if job.kind == 'build' else 'build']
            self.assertTrue(other.wait(5), f'{job.kind} never saw the other kind start')
            if job.target.get('item_id') == 1:
                self.assertTrue(hold.wait(5), 'first build never released')
            with lock:
                running.remove(job.kind)
            if both:
                log.line('overlap')

        jobs.register('build', h)
        jobs.register('ota', h)
        first = jobs.enqueue('build', {'item_id': 1}, 'manual')
        ota = jobs.enqueue('ota', {'device_id': 1}, 'manual')
        self.assertTrue(started['build'].wait(5) and started['ota'].wait(5), 'build and ota never ran together')
        second = jobs.enqueue('build', {'item_id': 2}, 'manual')
        self.assertEqual(jobs.get(second)['status'], 'queued')  # first build is held, so the build queue is blocked
        hold.set()
        ids = [first, ota, second]
        for i in ids:
            wait_status(i, {'success'})
        self.assertEqual(peak['build'], 1)
        self.assertEqual([x for x in entered if x != ota], [first, second])
        logs = ''.join((db.DATA / 'logs' / f'{i}.log').read_text() for i in ids)
        self.assertIn('overlap', logs)

    @needs_pidfd
    def test_cancel_kills_process_group(self):
        pidfile = self.data / 'child.pid'
        go = threading.Event()

        def h(job, log):
            log.run(['sh', '-c', f'tail -f /dev/null & echo $! > {pidfile}; echo go; wait'],
                    on_line=lambda s: go.set() if s == 'go' else None)

        jobs.register('build', h)
        jid = jobs.enqueue('build', {'item_id': 1}, 'manual')
        self.assertTrue(go.wait(5), 'child never printed go')
        self.assertEqual(jobs.cancel(jid), 'cancelling')
        j = wait_status(jid, {'cancelled'}, timeout=15)
        self.assertEqual(j['reason'], 'cancelled')
        assert_dead(self, int(pidfile.read_text()))

    @needs_pidfd
    def test_success_kills_leftover_group_members(self):
        pidfile = self.data / 'child.pid'
        # the leftover ignores SIGTERM and keeps no pipe open, so only an explicit group kill ends it
        jobs.register('build', lambda job, log: log.run(
            ['sh', '-c', f"(trap '' TERM; exec tail -f /dev/null) >/dev/null 2>&1 & echo $! > {pidfile}"]))
        wait_status(jobs.enqueue('build', {'item_id': 1}, 'manual'), {'success'})
        assert_dead(self, int(pidfile.read_text()))

    @needs_pidfd
    def test_on_line_raise_kills_process_group(self):
        pidfile = self.data / 'child.pid'

        def boom(s):
            if s == 'go':
                raise RuntimeError('callback failed')

        def h(job, log):
            log.run(['sh', '-c', f'tail -f /dev/null & echo $! > {pidfile}; echo go; wait'], on_line=boom)

        jobs.register('build', h)
        t0 = time.monotonic()
        j = wait_status(jobs.enqueue('build', {'item_id': 1}, 'manual'), {'failed'}, timeout=15)
        self.assertLess(time.monotonic() - t0, 15)
        assert_dead(self, int(pidfile.read_text()))

    def test_cancel_queued_never_runs(self):
        ran = []
        gate, running = threading.Event(), threading.Event()
        jobs.register('build', lambda job, log: (ran.append(job.id), running.set(), gate.wait(5)))
        first = jobs.enqueue('build', {'item_id': 1}, 'manual')
        self.assertTrue(running.wait(5), 'first job never started')
        second = jobs.enqueue('build', {'item_id': 2}, 'manual')
        self.assertEqual(jobs.cancel(second), 'cancelled')
        gate.set()
        wait_status(first, {'success'})
        third = jobs.enqueue('build', {'item_id': 3}, 'manual')  # claimed by id after the cancelled one
        wait_status(third, {'success'})
        self.assertEqual(ran, [first, third])
        self.assertEqual(jobs.get(second)['status'], 'cancelled')

    def test_enqueue_during_empty_claim_is_not_lost(self):
        """An enqueue between a worker's empty claim and its wait still wakes it (no wait for a timeout)."""
        claim, queued = jobs._claim, []

        def claim_then_enqueue(kind):
            job = claim(kind)
            if kind == 'build' and job is None and not queued:  # the window: claim saw nothing, wait not yet begun
                queued.append(jobs.enqueue('build', {'item_id': 1}, 'manual'))
            return job

        with mock.patch.object(jobs, '_claim', claim_then_enqueue):
            jobs.register('build', lambda job, log: None)  # wakes the idle worker into the patched claim
            wait_status(FIRST_JOB_ID, {'success'}, timeout=WAKE_LIMIT_S)
        self.assertEqual(queued, [FIRST_JOB_ID])

    def test_timeout(self):
        jobs.register('build', lambda job, log: log.run(['tail', '-f', '/dev/null'], timeout=0.5))
        j = wait_status(jobs.enqueue('build', {'item_id': 1}, 'manual'), {'failed'}, timeout=15)
        self.assertEqual(j['reason'], 'timeout')

    def test_ansi_stripped_and_on_line(self):
        seen = []
        jobs.register('build', lambda job, log: log.run(['printf', '\\033[32mok\\033[0m\\n::step::patch\\n'], on_line=seen.append))
        jid = jobs.enqueue('build', {'item_id': 1}, 'manual')
        wait_status(jid, {'success'})
        text = (db.DATA / 'logs' / f'{jid}.log').read_text()
        self.assertRegex(text, r'^\d\d:\d\d:\d\d ok\n')
        self.assertNotIn('\x1b', text)
        self.assertEqual(seen, ['ok', '::step::patch'])


class InlineTest(DataTestCase):
    def test_parity(self):
        def ok(job, log): pass
        def bad(job, log): raise jobs.JobFailed('patch_failed: x')
        def boom(job, log): raise ValueError('kaput\nmore')
        for h, status, reason in ((ok, 'success', None), (bad, 'failed', 'patch_failed: x'),
                                  (boom, 'failed', 'ValueError: kaput')):
            jobs.register('build', h)
            j = jobs.run_inline('build', {'item_id': 1}, 'manual')
            self.assertEqual((j['status'], j['reason']), (status, reason))


class RecoveryCleanupTest(DataTestCase):
    def test_recover(self):
        with db.tx() as c:
            c.execute("INSERT INTO jobs(kind,status,trigger,target,created_at) VALUES('build','running','manual','{}','x')")
            c.execute("INSERT INTO jobs(kind,status,trigger,target,created_at) VALUES('build','queued','manual','{}','x')")
        jobs.recover()
        rows = [tuple(r) for r in db.conn().execute('SELECT status, reason FROM jobs ORDER BY id')]
        self.assertEqual(rows, [('failed', 'server restarted'), ('queued', None)])

    def test_cleanup_skips_restrict_and_deletes_logs(self):
        settings.put('job_history_count', 1)
        db.conn().execute('CREATE TABLE keep (job_id INTEGER REFERENCES jobs(id) ON DELETE RESTRICT)')
        jobs.register('build', lambda job, log: log.line('x'))
        a = jobs.run_inline('build', {'item_id': 1}, 'manual')['id']
        db.conn().execute('INSERT INTO keep VALUES (?)', (a,))
        b = jobs.run_inline('build', {'item_id': 2}, 'manual')['id']
        c = jobs.run_inline('build', {'item_id': 3}, 'manual')['id']
        ids = [r[0] for r in db.conn().execute('SELECT id FROM jobs ORDER BY id')]
        self.assertEqual(ids, [a, c])            # b deleted, a kept by RESTRICT
        self.assertFalse((db.DATA / 'logs' / f'{b}.log').exists())
