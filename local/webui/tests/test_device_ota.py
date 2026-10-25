from unittest import mock

from webui import build, db, ota, projects
from webui.tests import helpers
from webui.tests.device_helpers import (FakeAdb, add_app_device, add_catalog_item, add_device, add_group, add_job, assign,
                                        pinfo, report, req)

YT, YTM, GMS = 'com.google.android.youtube', 'com.google.android.apps.youtube.music', 'app.revanced.android.gms'
OTHER = 'org.example.reader'


def art(item_id, job_id, abi='arm64-v8a', path='/x.apk'):
    return {'id': job_id * 10, 'job_id': job_id, 'item_id': item_id, 'variant': abi, 'abi': abi, 'package': YT,
            'version_name': '19.47.53', 'version_code': 1, 'patches_version': 'v1', 'signer_sha256': 'aa',
            'path': path, 'size': 1, 'sha256': 'bb', 'created_at': '2026-10-19T04:00:00Z'}


def seams(tc, deps=None, assigned=None, infos=None, latest=None):
    """Patches ota's project seams: deps {item_id: [pid]}, assigned [pid], infos {pid: pinfo}."""
    infos = infos or {}
    for mod, name, fn in ((projects, 'item_projects', lambda i: (deps or {}).get(i, [])),
                          (projects, 'assigned_projects', lambda d: list(assigned or [])),
                          (projects, 'project_info', lambda pid, abis: infos.get(pid)),
                          (build, 'latest_artifact', latest or (lambda i, abi: None))):
        tc.enterContext(mock.patch.object(mod, name, side_effect=fn))


def add_install(device_id, kind, ref_id, package, tag=None, build_job_id=None):
    jid = add_job(target={'device_id': device_id}, status='success')
    with db.tx() as c:
        c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,upstream_tag,package,result) "
                  "VALUES (?,?,?,?,?,?,?,'installed')", (jid, device_id, kind, ref_id, build_job_id, tag, package))


class PlanTest(helpers.DataTestCase):
    def items(self):
        return [{'item_id': 1, 'package': YT}, {'item_id': 2, 'package': YTM}]

    def kinds(self, steps):
        return [(s['kind'], s['ref_id'], s['action'], s['reason']) for s in steps]

    def test_plan_d7_build_job(self):
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: []}, [], {1: {'build_job_id': 5}}, {}, {YT})
        self.assertEqual([s['action'] for s in steps], ['up_to_date'])
        steps = ota.plan(self.items()[:1], {1: art(1, 6)}, {1: []}, [], {1: {'build_job_id': 5}}, {}, {YT})
        self.assertEqual((steps[0]['action'], steps[0]['reason']), ('install', 'new build'))

    def test_plan_missing_on_phone(self):
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: []}, [], {1: {'build_job_id': 5}}, {}, set())
        self.assertEqual((steps[0]['action'], steps[0]['reason']), ('install', 'missing on phone'))

    def test_plan_no_artifact_error(self):
        steps = ota.plan(self.items()[:1], {1: None}, {1: [pinfo()]}, [], {}, {}, None)
        self.assertEqual(self.kinds(steps), [('item', 1, 'error', 'NO_MATCHING_ARTIFACT')])

    def test_dependency_project_before_item(self):
        arts, deps = {1: art(1, 5), 2: art(2, 6)}, {1: [pinfo()], 2: [pinfo()]}
        steps = ota.plan(self.items(), arts, deps, [], {}, {}, {YT})
        self.assertEqual(self.kinds(steps), [('project', 1, 'install', 'missing on phone'),
                                             ('item', 1, 'install', 'new build'),
                                             ('item', 2, 'install', 'missing on phone')])
        self.assertEqual(steps[1]['needs'], [1])
        self.assertEqual(steps[0], {'kind': 'project', 'ref_id': 1, 'upstream_id': 23, 'package': GMS,
                                    'path': '/data/upstream/23/7.2.1/microg.apk', 'file': 'microg.apk',
                                    'abi': 'universal', 'tag': '7.2.1', 'version_name': '7.2.1',
                                    'version_code': 255070218, 'action': 'install', 'reason': 'missing on phone'})
        same = ota.plan(self.items(), arts, deps, [], {}, {1: {'upstream_tag': '7.2.1'}}, {YT, GMS})
        self.assertEqual([s['kind'] for s in same], ['item', 'item'])
        newer = ota.plan(self.items(), arts, {1: [pinfo(tag='7.2.2')]}, [], {}, {1: {'upstream_tag': '7.2.1'}},
                         {YT, GMS})
        self.assertEqual(self.kinds(newer)[0], ('project', 1, 'install', 'new release'))

    def test_direct_project_install_and_up_to_date(self):
        steps = ota.plan([], {}, {}, [pinfo()], {}, {}, {GMS})
        self.assertEqual(self.kinds(steps), [('project', 1, 'install', 'new release')])
        steps = ota.plan([], {}, {}, [pinfo()], {}, {1: {'upstream_tag': '7.2.1'}}, {GMS})
        self.assertEqual(self.kinds(steps), [('project', 1, 'up_to_date', 'same release installed')])

    def test_project_missing_on_phone(self):
        steps = ota.plan([], {}, {}, [pinfo()], {}, {1: {'upstream_tag': '7.2.1'}}, set())
        self.assertEqual(self.kinds(steps), [('project', 1, 'install', 'missing on phone')])

    def test_assigned_and_dependency_planned_once(self):
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: [pinfo()]}, [pinfo(), pinfo(2, OTHER)], {}, {}, None)
        self.assertEqual([(s['kind'], s['ref_id']) for s in steps], [('project', 1), ('item', 1), ('project', 2)])
        # item up to date: the dependency is not planned for it, but the direct assignment still lists it
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: [pinfo()]}, [pinfo()], {1: {'build_job_id': 5}},
                         {1: {'upstream_tag': '7.2.1'}}, None)
        self.assertEqual(self.kinds(steps), [('item', 1, 'up_to_date', 'same build installed'),
                                             ('project', 1, 'up_to_date', 'same release installed')])

    def test_signer_alert_error_step(self):
        p = pinfo(signer_alert='cd' * 32)
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: [p]}, [], {}, {}, None)
        self.assertEqual(self.kinds(steps), [('project', 1, 'error', 'SIGNER_CHANGED'), ('item', 1, 'install', 'new build')])
        steps = ota.plan([], {}, {}, [p], {}, {1: {'upstream_tag': '7.2.1'}}, None)
        self.assertEqual(self.kinds(steps), [('project', 1, 'error', 'SIGNER_CHANGED')])

    def test_no_matching_abi_error_step(self):
        steps = ota.plan([], {}, {}, [pinfo(path=None)], {}, {}, None)
        self.assertEqual(self.kinds(steps), [('project', 1, 'error', 'NO_MATCHING_ABI')])
        self.assertEqual((steps[0]['tag'], steps[0]['path']), ('7.2.1', None))

    def test_not_downloaded_install_without_path(self):
        steps = ota.plan([], {}, {}, [pinfo(tag=None)], {}, {}, {GMS})
        self.assertEqual(self.kinds(steps), [('project', 1, 'install', 'not downloaded')])
        self.assertIsNone(steps[0]['path'])

    def test_scope_project_ids_only(self):
        add_catalog_item(1, YT)
        d = add_device()
        assign(d, 1)
        seams(self, deps={1: [1]}, assigned=[1, 2], infos={1: pinfo(), 2: pinfo(2, OTHER)},
              latest=lambda i, abi: art(1, 5))
        steps = ota._plan_for(ota.devices.get(d), [], [2], None)['steps']
        self.assertEqual([(s['kind'], s['ref_id']) for s in steps], [('project', 2)])
        steps = ota._plan_for(ota.devices.get(d), None, None, None)['steps']
        self.assertEqual([(s['kind'], s['ref_id']) for s in steps], [('project', 1), ('item', 1), ('project', 2)])

    def test_plan_abi_choice_via_latest_artifact(self):
        add_catalog_item(1, YT)
        d = add_device(abi='x86_64,x86')
        assign(d, 1)
        FakeAdb(self)
        calls, pcalls = [], []
        seams(self, deps={1: [1]}, latest=lambda i, abi: calls.append(abi) or None)
        projects.project_info.side_effect = lambda pid, abis: pcalls.append(abis) or None
        ota.list_devices(req(method='GET'))
        self.assertEqual(calls, ['x86_64'])
        self.assertEqual(pcalls, [['x86_64', 'x86']])
        self.assertEqual(ota.abis_of({'abi_list': ''}), ['universal'])

    def test_assigned_items_catalog_order_incl_disabled(self):
        add_group(10, 1)
        add_group(20, 0)
        add_catalog_item(1, 'a.b.one', group_id=10, position=0)
        add_catalog_item(2, 'a.b.two', group_id=20, position=1)
        add_catalog_item(3, 'a.b.three', group_id=20, position=0, enabled=0)
        add_catalog_item(4, 'a.b.four', group_id=None, position=0)
        d = add_device()
        assign(d, 1, 2, 3, 4)
        self.assertEqual([i['item_id'] for i in ota.assigned_items(d)], [3, 2, 1, 4])
        self.assertEqual([i['item_id'] for i in ota.assigned_items(d, [4, 2])], [2, 4])

    def test_get_devices_no_adb(self):
        add_catalog_item(1, YT)
        d = add_device()
        assign(d, 1)
        add_install(d, 'item', 1, YT, build_job_id=5)
        fa = FakeAdb(self)
        fa.rule('devices', rc=1, out='error: cannot connect\n')
        seams(self, latest=lambda i, abi: art(1, 6))
        st, body = ota.list_devices(req(method='GET'))
        self.assertEqual(st, 200)
        dev = body['devices'][0]
        self.assertFalse(dev['online'])
        self.assertNotIn('dependencies', dev)
        a = dev['assignments'][0]
        self.assertEqual((a['item_id'], a['pending'], a['installed']['build_job_id'], a['available']['build_job_id']),
                         (1, True, 5, 6))
        self.assertEqual([c[0] for c in fa.calls()], ['devices'])

    def test_get_devices_app_row(self):
        add_catalog_item(1, YT)
        d = add_app_device(token_hash='ab' * 32, app_version_code=3)
        assign(d, 1)
        report(d, YT, 7, '19.47.53', 'aa')
        FakeAdb(self).rule('devices', out='List of devices attached\nNone:None\tdevice\n')
        seams(self, latest=lambda i, abi: art(1, 6))
        dev = ota.list_devices(req(method='GET'))[1]['devices'][0]
        self.assertIsNone(dev['online'])
        self.assertNotIn('token_hash', dev)
        self.assertEqual((dev['kind'], dev['app_version_code'], dev['last_seen_at']), ('app', 3, None))
        self.assertRegex(dev['created_at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$')
        self.assertEqual(dev['assignments'][0]['installed_reported'],
                         {'version_code': 7, 'version_name': '19.47.53', 'signer_sha256': 'aa',
                          'reported_at': '2026-10-25T08:00:00Z'})

    def test_projects_shape_and_update_available(self):
        add_catalog_item(1, YT)
        add_catalog_item(2, YTM)
        d = add_device()
        assign(d, 1, 2)
        add_install(d, 'project', 1, GMS, tag='7.2.1')
        add_install(d, 'project', 2, OTHER, tag='v1.4.0')
        add_install(d, 'item', 1, YT, build_job_id=5)
        add_install(d, 'item', 2, YTM, build_job_id=6)
        FakeAdb(self).rule('devices', rc=1, out='error\n')
        infos = {1: pinfo(tag='7.2.2', path='/u/23/7.2.2/microg-7.2.2.apk'),
                 2: pinfo(2, OTHER, tag='v1.4.0', path='/u/9/v1.4.0/reader.apk', upstream_id=9, abi='arm64-v8a',
                          display_name='Reader'),
                 3: pinfo(3, 'org.example.three', tag='2.0', path='/u/3/2.0/three.apk', upstream_id=3,
                          display_name='Three', signer_alert='ef' * 32)}
        seams(self, deps={1: [1], 2: [1]}, assigned=[3, 2], infos=infos,
              latest=lambda i, abi: art(i, 4 + i))
        dev = ota.list_devices(req(method='GET'))[1]['devices'][0]
        self.assertEqual(dev['projects'], [
            {'project_id': 1, 'display_name': 'MicroG RE', 'package': GMS, 'assigned': False, 'required_by': [1, 2],
             'installed_tag': '7.2.1', 'available_tag': '7.2.2', 'available_file': 'microg-7.2.2.apk',
             'available_abi': 'universal', 'update_available': True, 'pending': False, 'signer_alert': None,
             'installed_reported': None},
            {'project_id': 2, 'display_name': 'Reader', 'package': OTHER, 'assigned': True, 'required_by': [],
             'installed_tag': 'v1.4.0', 'available_tag': 'v1.4.0', 'available_file': 'reader.apk',
             'available_abi': 'arm64-v8a', 'update_available': False, 'pending': False, 'signer_alert': None,
             'installed_reported': None},
            {'project_id': 3, 'display_name': 'Three', 'package': 'org.example.three', 'assigned': True,
             'required_by': [], 'installed_tag': None, 'available_tag': '2.0', 'available_file': 'three.apk',
             'available_abi': 'universal', 'update_available': False, 'pending': False, 'signer_alert': 'ef' * 32,
             'installed_reported': None}])
        # pending follows the full plan: a direct project with a newer release is an install step
        newer = dict(infos)
        newer[2] = pinfo(2, OTHER, tag='v1.5.0', path='/u/9/v1.5.0/reader.apk', upstream_id=9)
        projects.project_info.side_effect = lambda pid, abis: newer.get(pid)
        p2 = ota.list_devices(req(method='GET'))[1]['devices'][0]['projects'][1]
        self.assertEqual((p2['update_available'], p2['pending']), (True, True))

    def test_update_available_with_v_prefixed_tags(self):
        d = add_device()
        add_install(d, 'project', 1, GMS, tag='v1.9')
        add_install(d, 'project', 2, OTHER, tag='v2.0')
        FakeAdb(self).rule('devices', rc=1, out='error\n')
        seams(self, assigned=[1, 2], infos={1: pinfo(tag='v2.0', path='/u/23/v2.0/microg.apk'),
                                            2: pinfo(2, OTHER, tag='v1.9', path='/u/9/v1.9/reader.apk', upstream_id=9)})
        dps = ota.list_devices(req(method='GET'))[1]['devices'][0]['projects']
        self.assertEqual([(p['installed_tag'], p['available_tag'], p['update_available']) for p in dps],
                         [('v1.9', 'v2.0', True), ('v2.0', 'v1.9', False)])
