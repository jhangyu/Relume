from unittest import mock

from webui import db, ota
from webui.tests import helpers
from webui.tests.device_helpers import (FakeAdb, add_catalog_item, add_device, add_group, add_job, assign, req)

YT, YTM, GMS = 'com.google.android.youtube', 'com.google.android.apps.youtube.music', 'app.revanced.android.gms'
MICROG = {'dependency_app_id': 1, 'upstream_id': 7, 'package': GMS, 'display_name': 'MicroG RE',
          'tag': 'v7.2.1', 'path': '/data/upstream/7/v7.2.1/microg-7.2.1.apk', 'signer_sha256': None}


def art(item_id, job_id, abi='arm64-v8a', path='/x.apk'):
    return {'id': job_id * 10, 'job_id': job_id, 'item_id': item_id, 'variant': abi, 'abi': abi, 'package': YT,
            'version_name': '19.47.53', 'version_code': 1, 'patches_version': 'v1', 'signer_sha256': 'aa',
            'path': path, 'size': 1, 'sha256': 'bb', 'created_at': '2026-10-19T04:00:00Z'}


class PlanTest(helpers.DataTestCase):
    def items(self):
        return [{'item_id': 1, 'package': YT}, {'item_id': 2, 'package': YTM}]

    def test_plan_d7_build_job(self):
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: []}, {1: {'build_job_id': 5}}, {}, {YT})
        self.assertEqual([s['action'] for s in steps], ['up_to_date'])
        steps = ota.plan(self.items()[:1], {1: art(1, 6)}, {1: []}, {1: {'build_job_id': 5}}, {}, {YT})
        self.assertEqual((steps[0]['action'], steps[0]['reason']), ('install', 'new build'))

    def test_plan_missing_on_phone(self):
        steps = ota.plan(self.items()[:1], {1: art(1, 5)}, {1: []}, {1: {'build_job_id': 5}}, {}, set())
        self.assertEqual((steps[0]['action'], steps[0]['reason']), ('install', 'missing on phone'))

    def test_plan_d8_tag_and_dedup(self):
        steps = ota.plan(self.items(), {1: art(1, 5), 2: art(2, 6)}, {1: [MICROG], 2: [MICROG]}, {}, {}, {YT})
        self.assertEqual([(s['kind'], s['ref_id']) for s in steps], [('dependency', 7), ('item', 1), ('item', 2)])
        self.assertEqual(steps[1]['needs'], [7])
        same = ota.plan(self.items(), {1: art(1, 5), 2: art(2, 6)}, {1: [MICROG], 2: [MICROG]}, {},
                        {7: {'upstream_tag': 'v7.2.1'}}, {YT, GMS})
        self.assertEqual([s['kind'] for s in same], ['item', 'item'])

    def test_plan_no_artifact_error(self):
        steps = ota.plan(self.items()[:1], {1: None}, {1: [MICROG]}, {}, {}, None)
        self.assertEqual([(s['kind'], s['action'], s['reason']) for s in steps],
                         [('item', 'error', 'NO_MATCHING_ARTIFACT')])

    def test_plan_abi_choice_via_latest_artifact(self):
        add_catalog_item(1, YT)
        d = add_device(abi='x86_64,x86')
        assign(d, 1)
        FakeAdb(self)
        calls = []
        with mock.patch.object(ota, '_latest_artifact', side_effect=lambda i, abi: calls.append(abi) or None), \
                mock.patch.object(ota, '_item_dependencies', return_value=[]):
            ota.list_devices(req(method='GET'))
        self.assertEqual(calls, ['x86_64'])

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
        jid = add_job(target={'device_id': d}, status='success')
        with db.tx() as c:
            c.execute("INSERT INTO ota_installs(job_id,device_id,kind,ref_id,build_job_id,package,version_name,result) "
                      "VALUES (?,?,'item',1,5,?,'19.0','installed')", (jid, d, YT))
        fa = FakeAdb(self)
        fa.rule('devices', rc=1, out='error: cannot connect\n')
        with mock.patch.object(ota, '_latest_artifact', return_value=art(1, 6)), \
                mock.patch.object(ota, '_item_dependencies', return_value=[MICROG]):
            st, body = ota.list_devices(req(method='GET'))
        self.assertEqual(st, 200)
        dev = body['devices'][0]
        self.assertFalse(dev['online'])
        a = dev['assignments'][0]
        self.assertEqual((a['item_id'], a['pending'], a['installed']['build_job_id'], a['available']['build_job_id']),
                         (1, True, 5, 6))
        self.assertEqual(dev['dependencies'], [{'dependency_app_id': 1, 'display_name': 'MicroG RE', 'package': GMS,
                                                'installed_tag': None, 'available_tag': 'v7.2.1', 'required_by': [1]}])
        self.assertEqual([c[0] for c in fa.calls()], ['devices'])

    def test_plan_route(self):
        add_catalog_item(1, YT)
        d = add_device()
        assign(d, 1)
        fa = FakeAdb(self)
        fa.rule('devices', out='List of devices attached\n192.168.1.20:37011\tdevice\n')
        fa.rule('pm', out='package:com.android.settings\n')
        with mock.patch.object(ota, '_latest_artifact', return_value=art(1, 6)), \
                mock.patch.object(ota, '_item_dependencies', return_value=[MICROG]):
            st, body = ota.plan_route(req(method='GET', params={'id': d}))
        self.assertTrue(body['online'])
        self.assertEqual([(s['kind'], s['reason']) for s in body['steps']],
                         [('dependency', 'missing on phone'), ('item', 'missing on phone')])
