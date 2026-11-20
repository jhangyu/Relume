import unittest

from webui import appmanage_matrix as m


def entry(vc=1, vn='1', sizes=(1, 1, 1), label='L', icon=None):
    a, d, c = sizes if sizes else (None, None, None)
    return {'version_code': vc, 'version_name': vn, 'app_bytes': a, 'data_bytes': d, 'cache_bytes': c,
            'size_reason': None if sizes else 'no access', 'label': label, 'icon_sha256': icon}


def act(id, device_id, package, state, action='upgrade'):
    return {'id': id, 'device_id': device_id, 'package': package, 'action': action, 'source': 'play_direct',
            'state': state, 'error_code': None, 'error_text': None, 'created_at': 'c', 'updated_at': 'u'}


def dev(id, name='d'):
    return {'id': id, 'name': name, 'kind': 'adb', 'online': True, 'reported_at': None}


class RowOrder(unittest.TestCase):
    def test_groups_sizes_unknown_and_tie_break(self):
        apps = {
            'off.small': {2: entry(sizes=(1, 0, 0))},
            'off.big': {2: entry(sizes=(9, 0, 0)), 3: entry(sizes=(2, 0, 0))},
            'off.0unknown': {2: entry(sizes=None)},
            'off.zero': {2: entry(sizes=(0, 0, 0))},
            'off.b': {2: entry(sizes=(5, 0, 0))},
            'off.a': {3: entry(sizes=(5, 0, 0))},
            'on.small': {1: entry(sizes=(1, 0, 0)), 2: entry(sizes=(99, 0, 0))},
            'on.big': {1: entry(sizes=(7, 0, 0))},
            'on.0unknown': {1: entry(sizes=None)},
        }
        self.assertEqual(
            m.row_order(apps, 1, [2, 3]),
            ['off.big', 'off.a', 'off.b', 'off.small', 'off.zero', 'off.0unknown', 'on.big', 'on.small', 'on.0unknown'])

    def test_no_primary_everything_off_primary(self):
        apps = {'b': {2: entry(sizes=(1, 0, 0))}, 'a': {2: entry(sizes=(1, 0, 0))}, 'c': {2: entry(sizes=(3, 0, 0))}}
        self.assertEqual(m.row_order(apps, None, [2]), ['c', 'a', 'b'])


class Highest(unittest.TestCase):
    def test_device_vs_build(self):
        inv = {1: {'p': entry(5, 'five')}, 2: {'p': entry(7, 'seven')}}
        self.assertEqual(m.highest('p', inv, {}), {'version_code': 7, 'version_name': 'seven', 'from': 'device'})
        tie = {'p': {'kind': 'item', 'id': 1, 'version_code': 7, 'version_name': 'b7'}}
        self.assertEqual(m.highest('p', inv, tie)['from'], 'device')
        win = {'p': {'kind': 'item', 'id': 1, 'version_code': 8, 'version_name': 'b8'}}
        self.assertEqual(m.highest('p', inv, win), {'version_code': 8, 'version_name': 'b8', 'from': 'build'})
        self.assertIsNone(m.highest('q', inv, {}))


class Build(unittest.TestCase):
    def setUp(self):
        self.devices = [dev(3, 'primary'), dev(1, 'tab')]
        self.inv = {3: {'p': entry(1, '1', label='Primary label', icon='a' * 64)},
                    1: {'p': entry(2, '2', label='Tab label'), 'q': entry(1, '1', label=None)}}

    def test_outdated_on_primary_column_and_labels(self):
        out = m.build(self.devices, self.inv, [], {}, 3)
        rows = {r['package']: r for r in out['apps']}
        self.assertTrue(rows['p']['cells']['3']['installed']['outdated'])
        self.assertFalse(rows['p']['cells']['1']['installed']['outdated'])
        self.assertEqual((rows['p']['label'], rows['p']['icon']), ('Primary label', 'a' * 64))
        self.assertEqual((rows['q']['label'], rows['q']['icon']), ('q', None))
        self.assertEqual((rows['q']['group'], rows['p']['group']), ('off_primary', 'on_primary'))
        self.assertIsNone(rows['q']['cells']['3']['installed'])

    def test_source_and_ref(self):
        tracked = {'p': {'kind': 'item', 'id': 4, 'version_code': 3, 'version_name': '3'}}
        rows = {r['package']: r for r in m.build(self.devices, self.inv, [], tracked, 3)['apps']}
        self.assertEqual((rows['p']['source'], rows['p']['ref']), ('build', {'kind': 'item', 'id': 4}))
        self.assertEqual((rows['q']['source'], rows['q']['ref']), ('play_direct', None))

    def test_shown_action(self):
        actions = [act(1, 1, 'p', 'failed'), act(2, 1, 'p', 'queued'),   # open beats failed
                   act(3, 3, 'p', 'done'),                                # done hidden
                   act(4, 1, 'q', 'failed'), act(5, 1, 'q', 'done'),      # newest is done: nothing
                   act(6, 3, 'q', 'cancelled'), act(7, 3, 'q', 'failed'),
                   act(8, 1, 'only', 'running', 'install')]               # not in any inventory
        rows = {r['package']: r for r in m.build(self.devices, self.inv, actions, {}, 3)['apps']}
        self.assertEqual(rows['p']['cells']['1']['action']['id'], 2)
        self.assertIsNone(rows['p']['cells']['3']['action'])
        self.assertIsNone(rows['q']['cells']['1']['action'])
        self.assertEqual(rows['q']['cells']['3']['action']['id'], 7)
        self.assertEqual(rows['only']['cells']['1']['action']['state'], 'running')
        self.assertIsNone(rows['only']['highest'])


if __name__ == '__main__':
    unittest.main()
