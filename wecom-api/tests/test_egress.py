import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('wecom', Path(__file__).resolve().parents[1] / 'scripts/wecom.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def sa(ip='10.10.10.2', reqid='1', state='INSTALLED', ike='digibridge'):
    return (f'{ike}: #8, ESTABLISHED, IKEv1, xx\n'
            f"  local  '10.0.0.12' @ 10.0.0.12[4500] [{ip}]\n"
            f'  digibridge: #8, reqid {reqid}, {state}, TUNNEL-in-UDP, ESP:AES\n'
            f'    local  {ip}/32\n    remote 0.0.0.0/0\n')


def policy(ip='10.10.10.2', reqid='1', direction='out'):
    return (f'src {ip}/32 dst 0.0.0.0/0\n\tdir {direction} priority 1\n'
            f'\ttmpl src 10.0.0.12 dst 58.34.195.158\n'
            f'\t\tproto esp reqid {reqid} mode tunnel\n')


class DynamicSourceTests(unittest.TestCase):
    profile = {'source_ip': 'auto', 'status': ['swanctl', '--list-sas', '--ike', 'digibridge']}

    def resolve(self, sas=None, policies=None, local=None, status_rc=0, policy_rc=0):
        def run(command, **kwargs):
            if command == self.profile['status']:
                return status_rc, sas if sas is not None else sa()
            if command == ['ip', '-4', 'xfrm', 'policy']:
                return policy_rc, policies if policies is not None else policy()
            raise AssertionError(command)
        with patch.object(w, 'run_step', side_effect=run), patch.object(w, 'audit'), patch.object(w, 'local_addresses', return_value=local if local is not None else {'10.10.10.2'}):
            return w.egress_transport('office', self.profile)

    def test_current_lease_2(self):
        self.assertEqual(self.resolve(), {'source_ip': '10.10.10.2'})

    def test_changed_lease_4(self):
        self.assertEqual(self.resolve(sa('10.10.10.4'), policy('10.10.10.4'), {'10.10.10.4'}), {'source_ip': '10.10.10.4'})

    def test_stale_address_still_local_does_not_win(self):
        self.assertEqual(self.resolve(local={'10.10.10.2', '10.10.10.4'}), {'source_ip': '10.10.10.2'})

    def test_missing_local_lease_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(local={'10.0.0.12'})

    def test_missing_policy_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(policies='')

    def test_wrong_reqid_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(policies=policy(reqid='2'))

    def test_inbound_policy_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(policies=policy(direction='in'))

    def test_wrong_tunnel_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(sas=sa(ike='other-customer'))

    def test_child_not_installed_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(sas=sa(state='REKEYING'))

    def test_split_tunnel_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(sas=sa().replace('remote 0.0.0.0/0', 'remote 192.168.0.0/16'))

    def test_two_different_leases_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(sa()+sa('10.10.10.4','2'), policy()+policy('10.10.10.4','2'), {'10.10.10.2','10.10.10.4'})

    def test_rekey_same_source_deduplicated(self):
        self.assertEqual(self.resolve(sa()+sa(reqid='2'), policy()+policy(reqid='2')), {'source_ip':'10.10.10.2'})

    def test_failed_status_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(status_rc=1)

    def test_failed_policy_query_rejected(self):
        with self.assertRaises(w.WecomError): self.resolve(policy_rc=1)

    def test_unscoped_status_rejected(self):
        with self.assertRaises(w.WecomError): w.egress_transport('office', {'source_ip':'auto','status':['swanctl','--list-sas']})

    def test_static_and_proxy_unchanged(self):
        self.assertEqual(w.egress_transport('office', {'source_ip':'10.10.10.4'}), {'source_ip':'10.10.10.4'})
        self.assertEqual(w.egress_transport('office', {'url':'http://127.0.0.1:80'}), {'proxy':'http://127.0.0.1:80'})

    def test_resolution_after_dial(self):
        events=[]
        p={**self.profile,'up':['dial']}
        def run(cmd, **kwargs):
            events.append('dial'); return 0,''
        def resolve(*args):
            events.append('resolve'); return {'source_ip':'10.10.10.2'}
        from contextlib import nullcontext
        with patch.object(w,'load_egress_profile',return_value=p), patch.object(w,'EgressLock',return_value=nullcontext()), patch.object(w,'egress_is_up',side_effect=[False,True]), patch.object(w,'run_step',side_effect=run), patch.object(w,'egress_transport',side_effect=resolve), patch.object(w,'local_addresses',return_value={'10.10.10.2'}), patch.object(w,'audit'), patch.object(w,'read_egress_state',return_value={}), patch.object(w,'write_private'):
            w.acquire_egress('office')
        self.assertEqual(events,['dial','resolve'])

if __name__ == '__main__': unittest.main()
