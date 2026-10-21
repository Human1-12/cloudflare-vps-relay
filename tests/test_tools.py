import copy
import unittest
import uuid
from urllib.parse import urlencode
import deploy
from make_clients import parse_direct_link, paired_configs


class ToolsTests(unittest.TestCase):
    def plan(self):
        return dict(account_id='a' * 32, zone_id='b' * 32, worker_name='cf-vps-relay',
                    hostname='relay.example.com', origin_host='vps.example.com', origin_port=50001)

    def link(self, **overrides):
        query = dict(security='reality', type='tcp', pbk='example-test-public-key',
                     sni='example.com', fp='chrome', flow='xtls-rprx-vision')
        query.update(overrides)
        return 'vless://00112233-4455-6677-8899-aabbccddeeff@vps.example.com:50001?' + urlencode(query)

    def test_plan_rejects_loop_and_private_ip(self):
        plan = self.plan()
        plan['hostname'] = plan['origin_host']
        with self.assertRaises(ValueError): deploy.validate_plan(plan)
        for host in ['127.0.0.1', '10.0.0.1', 'localhost', 'example.com/path', 'a..example.com']:
            with self.subTest(host=host), self.assertRaises(ValueError): deploy.valid_host(host)

    def test_plan_rejects_bad_account_port_and_name(self):
        for key, value in [('account_id', 'bad'), ('origin_port', 0), ('worker_name', '../../bad')]:
            plan = self.plan()
            plan[key] = value
            with self.assertRaises(ValueError): deploy.validate_plan(plan)

    def test_direct_and_relay_preserve_same_inner_identity(self):
        original = parse_direct_link(self.link())
        before = copy.deepcopy(original)
        state = dict(self.plan(), relay_uuid=str(uuid.uuid4()))
        a, b = paired_configs(original, state)
        self.assertEqual(original, before)
        self.assertEqual(a['outbounds'][0]['settings'], b['outbounds'][0]['settings'])
        self.assertEqual(a['outbounds'][0]['streamSettings']['realitySettings'], b['outbounds'][0]['streamSettings']['realitySettings'])
        self.assertEqual(b['outbounds'][0]['streamSettings']['sockopt']['dialerProxy'], 'cf-front')
        self.assertNotIn('dialerProxy', a['outbounds'][0]['streamSettings']['sockopt'])
        self.assertEqual(len(b['outbounds']), 2)
        self.assertTrue(all(o['protocol'] != 'freedom' for o in b['outbounds']))

    def test_wrong_target_and_unsupported_protocol_are_rejected(self):
        state = dict(self.plan(), relay_uuid=str(uuid.uuid4()), origin_port=443)
        with self.assertRaises(ValueError): paired_configs(parse_direct_link(self.link()), state)
        with self.assertRaises(ValueError): parse_direct_link(self.link(type='ws'))
        with self.assertRaises(ValueError): parse_direct_link(self.link(allowInsecure='1'))

    def test_multipart_uses_secret_binding_and_modules(self):
        secret = str(uuid.uuid4())
        body, content_type = deploy.multipart(self.plan(), secret)
        self.assertIn(b'"type": "secret_text"', body)
        self.assertIn(b'filename="worker.mjs"', body)
        self.assertIn(b'filename="protocol.mjs"', body)
        self.assertIn(b'"compatibility_date": "2026-10-21"', body)
        self.assertTrue(content_type.startswith('multipart/form-data; boundary='))

    def test_zone_discovery_selects_owned_longest_suffix(self):
        class FakeCloudflare:
            def call(self, method, path):
                self.request = (method, path)
                return [{'name':'unrelated.example.org','id':'c'*32,'account':{'id':'d'*32}},
                        {'name':'example.com','id':'b'*32,'account':{'id':'a'*32}}]
        plan = self.plan()
        plan.update(account_id='',zone_id='')
        resolved = deploy.resolve_zone(FakeCloudflare(), plan)
        self.assertEqual(resolved['account_id'], 'a'*32)
        self.assertEqual(resolved['zone_id'], 'b'*32)

    def test_physical_source_binding_is_only_on_outer_socket(self):
        state = dict(self.plan(), relay_uuid=str(uuid.uuid4()))
        a,b = paired_configs(parse_direct_link(self.link()),state,'192.168.1.10')
        self.assertEqual(a['outbounds'][0]['sendThrough'],'192.168.1.10')
        self.assertEqual(b['outbounds'][-1]['sendThrough'],'192.168.1.10')
        self.assertNotIn('sendThrough',b['outbounds'][0])


if __name__ == '__main__': unittest.main()
