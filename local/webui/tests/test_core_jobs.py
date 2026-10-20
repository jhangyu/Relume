import json, os, pathlib, sqlite3, threading, time, unittest
from webui import db, jobs, settings
from webui.tests.helpers import DataTestCase


def wait_status(job_id, statuses, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        j = jobs.get(job_id)
        if j and j['status'] in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f'job {job_id} not in {statuses}: {jobs.get(job_id)}')


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
        self.assertEqual(jobs.get(jid)['options'], {'pull': True, 'rebuild': True, 'notify': True})
        raw = db.conn().execute('SELECT options, target FROM jobs WHERE id=?', (jid,)).fetchone()
        self.assertEqual(raw['options'], '{"notify":true,"pull":true,"rebuild":true}')
        self.assertEqual(raw['target'], '{"upstream_id":3}')


class WorkerTest(DataTestCase):
    def setUp(self):
        super().setUp()
        jobs.start()

    def test_serial_per_kind_parallel_across(self):
        running, peak, lock = [], {'build': 0, 'ota': 0}, threading.Lock()

        def h(job, log):
            with lock:
                running.append(job.kind)
                peak[job.kind] = max(peak[job.kind], running.count(job.kind))
                both = 'build' in running and 'ota' in running
            time.sleep(0.3)
            with lock:
                running.remove(job.kind)
            if both:
                log.line('overlap')

        jobs.register('build', h)
        jobs.register('ota', h)
        ids = [jobs.enqueue('build', {'item_id': i}, 'manual') for i in (1, 2)]
        ids.append(jobs.enqueue('ota', {'device_id': 1}, 'manual'))
        for i in ids:
            wait_status(i, {'success'})
        self.assertEqual(peak['build'], 1)
        logs = ''.join((db.DATA / 'logs' / f'{i}.log').read_text() for i in ids)
        self.assertIn('overlap', logs)

    def test_cancel_kills_process_group(self):
        pidfile = self.data / 'child.pid'

        def h(job, log):
            log.run(['sh', '-c', f'sleep 60 & echo $! > {pidfile}; wait'])

        jobs.register('build', h)
        jid = jobs.enqueue('build', {'item_id': 1}, 'manual')
        wait_status(jid, {'running'})
        end = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < end:
            time.sleep(0.05)
        self.assertEqual(jobs.cancel(jid), 'cancelling')
        j = wait_status(jid, {'cancelled'}, timeout=15)
        self.assertEqual(j['reason'], 'cancelled')
        pid = int(pidfile.read_text())
        time.sleep(0.2)
        # ponytail: the orphaned sleep is reparented to PID 1 (python in the test container, no reaper),
        # so a killed child stays a zombie (state Z); dead either way
        try:
            state = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            state = 'gone'
        self.assertIn(state, ('gone', 'Z'))

    def test_on_line_raise_kills_process_group(self):
        pidfile = self.data / 'child.pid'

        def boom(s):
            if s == 'go':
                raise RuntimeError('callback failed')

        def h(job, log):
            log.run(['sh', '-c', f'sleep 60 & echo $! > {pidfile}; echo go; wait'], on_line=boom)

        jobs.register('build', h)
        t0 = time.monotonic()
        j = wait_status(jobs.enqueue('build', {'item_id': 1}, 'manual'), {'failed'}, timeout=15)
        self.assertLess(time.monotonic() - t0, 15)
        pid = int(pidfile.read_text())
        try:  # same zombie note as test_cancel_kills_process_group
            state = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except FileNotFoundError:
            state = 'gone'
        self.assertIn(state, ('gone', 'Z'))

    def test_cancel_queued_never_runs(self):
        ran = []
        gate = threading.Event()
        jobs.register('build', lambda job, log: (ran.append(job.id), gate.wait(5)))
        first = jobs.enqueue('build', {'item_id': 1}, 'manual')
        wait_status(first, {'running'})
        second = jobs.enqueue('build', {'item_id': 2}, 'manual')
        self.assertEqual(jobs.cancel(second), 'cancelled')
        gate.set()
        wait_status(first, {'success'})
        time.sleep(0.3)
        self.assertEqual(ran, [first])
        self.assertEqual(jobs.get(second)['status'], 'cancelled')

    def test_timeout(self):
        jobs.register('build', lambda job, log: log.run(['sleep', '30'], timeout=0.5))
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
