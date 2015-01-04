#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import sys
import unittest
import base58
import binascii
from argparse import Namespace
import json

from six.moves.urllib.error import URLError

import mock
from datetime import datetime, timedelta

from downstream_farmer import utils, shell
from downstream_farmer.shell import Farmer, save, restore, API
from downstream_farmer.client import DownstreamClient, DownstreamContract
from downstream_farmer.exc import DownstreamError
from heartbeat import Heartbeat


class TestUtils(unittest.TestCase):

    def test_urlify(self):
        test_str = "arbitrary strings 'n shit"
        result = utils.urlify(test_str)
        self.assertEqual('arbitrary%20strings%20%27n%20shit', result)

    def test_handle_json_response(self):
        m = mock.MagicMock()
        m.status_code = 400
        m.json.return_value = dict(message='test error')
        with self.assertRaises(DownstreamError) as ex:
            utils.handle_json_response(m)
        self.assertEqual(str(ex.exception), 'test error')

        m.json.side_effect = Exception('json processing error')
        m.raise_for_status.side_effect = Exception('http error')
        with self.assertRaises(Exception) as ex:
            utils.handle_json_response(m)
        self.assertEqual(str(ex.exception), 'http error')

        m = mock.MagicMock()
        m.json.return_value = dict(key='value')
        result = utils.handle_json_response(m)
        self.assertEqual(m.json.return_value, result)


class TestContract(unittest.TestCase):

    def setUp(self):
        self.challenge = Heartbeat.challenge_type().\
            fromdict(MockValues.get_challenge_response['challenge'])
        self.heartbeat = Heartbeat.fromdict(
            MockValues.connect_response['heartbeat'])
        self.tag = Heartbeat.tag_type().fromdict(
            MockValues.get_chunk_response['tag'])
        self.expiration = datetime.utcnow(
        ) + timedelta(int(MockValues.get_chunk_response['due']))
        self.client = mock.MagicMock()
        self.api = API()
        self.contract = DownstreamContract(self.client,
                                           'hash',
                                           'seed',
                                           100,
                                           self.challenge,
                                           self.expiration,
                                           self.tag,
                                           self.api)

    def tearDown(self):
        pass

    def test_initialization(self):
        self.assertEqual(self.contract.client, self.client)
        self.assertEqual(self.contract.hash, 'hash')
        self.assertEqual(self.contract.seed, 'seed')
        self.assertEqual(self.contract.size, 100)
        self.assertEqual(self.contract.challenge, self.challenge)
        self.assertEqual(self.contract.expiration, self.expiration)
        self.assertEqual(self.contract.tag, self.tag)
        self.assertEqual(self.contract.answered, False)

    def test_time_remaining(self):
        self.assertEqual(self.contract.time_remaining(), 0)

        with mock.patch('downstream_farmer.contract.datetime') as patch:
            patch.utcnow.return_value = datetime.utcnow()
            self.contract.answered = True
            dt = self.contract.expiration - patch.utcnow.return_value
            result = self.contract.time_remaining()
        self.assertEqual(result, dt.total_seconds())

    def test_update_challenge_answered(self):
        # answered is false, so this should return immediately with no issues
        self.assertIsNone(self.contract.update_challenge())

    def test_update_challenge_block(self):
        self.contract.answered = True
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 1
        with mock.patch('time.sleep') as sleeppatch,\
                mock.patch(
                    'downstream_farmer.contract.requests.get') as getpatch:
            getpatch.return_value.json.return_value =\
                MockValues.get_challenge_response
            self.contract.update_challenge()
            self.assertTrue(sleeppatch.called)

    def test_update_challenge_no_block(self):
        self.contract.answered = True
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 1
        with mock.patch('time.sleep') as sleeppatch,\
                mock.patch(
                    'downstream_farmer.contract.requests.get') as getpatch:
            getpatch.return_value.json.return_value =\
                MockValues.get_challenge_response
            self.contract.update_challenge(block=False)
            self.assertFalse(sleeppatch.called)

    def test_update_challenge_get_fail(self):
        self.contract.answered = True
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 0
        with mock.patch('downstream_farmer.contract.requests.get') as getpatch:
            getpatch.side_effect = Exception('error')
            with self.assertRaises(DownstreamError) as ex:
                self.contract.update_challenge()
            self.assertEqual(str(ex.exception), 'Unable to perform HTTP get.')

    def test_update_challenge_failed(self):
        self.contract.answered = True
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 0
        with mock.patch(
                'downstream_farmer.contract.requests.get'),\
                mock.patch('downstream_farmer.contract.handle_json_response')\
                as hpatch:
            hpatch.side_effect = DownstreamError('error')
            with self.assertRaises(DownstreamError) as ex:
                self.contract.update_challenge()
            self.assertEqual(str(ex.exception), 'Challenge update failed.')

    def test_update_challenge_malformed(self):
        self.contract.answered = True
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 0
        with mock.patch('downstream_farmer.contract.requests.get'),\
                mock.patch('downstream_farmer.contract.handle_json_response')\
                as hpatch:
            hpatch.return_value = dict(invalid='dict')
            with self.assertRaises(DownstreamError) as ex:
                self.contract.update_challenge()
            self.assertEqual(
                str(ex.exception), 'Malformed response from server.')

    def test_update_challenge_working(self):
        self.contract.answered = True
        self.client.heartbeat = self.heartbeat
        self.api = API()
        self.contract.time_remaining = mock.MagicMock()
        self.contract.time_remaining.return_value = 0
        with mock.patch('downstream_farmer.contract.requests.get') as getpatch:
            getpatch.return_value.json.return_value =\
                MockValues.get_challenge_response
            self.contract.update_challenge()
            self.assertEqual(self.contract.challenge,
                             Heartbeat.challenge_type().fromdict(
                                 MockValues
                                 .get_challenge_response['challenge']),
                             self.api)
            self.assertAlmostEqual((self.
                                    contract.expiration - datetime.utcnow()).
                                   total_seconds(),
                                   int(MockValues.
                                       get_challenge_response['due']),
                                   delta=0.5)

    def test_answer_challenge_answered(self):
        self.contract.answered = True
        self.assertIsNone(self.contract.answer_challenge())

    def test_answer_challenge_post_fail(self):
        self.client.heartbeat = self.heartbeat
        with mock.patch('downstream_farmer.contract.requests.post') as ppatch:
            ppatch.side_effect = Exception('test error')
            with self.assertRaises(DownstreamError) as ex:
                self.contract.answer_challenge()
            self.assertEqual(str(ex.exception), 'Unable to perform HTTP post.')

    def test_answer_challenge_failed(self):
        self.client.heartbeat = self.heartbeat
        with mock.patch('downstream_farmer.contract.requests.post'),\
                mock.patch('downstream_farmer.contract.handle_json_response')\
                as hpatch:
            hpatch.side_effect = DownstreamError('test error')
            with self.assertRaises(DownstreamError) as ex:
                self.contract.answer_challenge()
            self.assertEqual(
                str(ex.exception), 'Challenge answer failed: test error')

    def test_answer_malformed(self):
        self.client.heartbeat = self.heartbeat
        with mock.patch('downstream_farmer.contract.requests.post') as patch:
            inst = patch.return_value
            inst.json.return_value = {"invalid": "dict"}
            with self.assertRaises(DownstreamError) as ex:
                self.contract.answer_challenge()
            self.assertEqual(
                str(ex.exception), 'Malformed response from server.')

    def test_answer_invalid(self):
        self.client.heartbeat = self.heartbeat
        with mock.patch('downstream_farmer.contract.requests.post') as patch:
            inst = patch.return_value
            inst.json.return_value = {"status": "dict"}
            with self.assertRaises(DownstreamError) as ex:
                self.contract.answer_challenge()
            self.assertEqual(str(ex.exception), 'Challenge response rejected.')

    def test_answer_working(self):
        self.client.heartbeat = self.heartbeat
        with mock.patch('downstream_farmer.contract.requests.post') as patch:
            inst = patch.return_value
            inst.json.return_value = {"status": "ok"}
            self.contract.answer_challenge()
            self.assertTrue(self.contract.answered)


class TestClient(unittest.TestCase):

    def setUp(self):
        self.server_url = 'https://test.url/'
        self.api_path = '/api/downstream/v1'
        self.size = 100
        self.address = base58.b58encode_check(b'\x00' + os.urandom(20))
        self.token = binascii.hexlify(os.urandom(16)).decode('ascii')
        self.msg = ''
        self.sig = ''
        self.api = API()
        self.client = DownstreamClient(self.server_url,
                                       self.token,
                                       self.address,
                                       self.size,
                                       self.msg,
                                       self.sig,
                                       self.api)
        self.test_contract = DownstreamContract(self.client,
                                                MockValues.get_chunk_response[
                                                    'file_hash'],
                                                MockValues.get_chunk_response[
                                                    'seed'],
                                                MockValues.get_chunk_response[
                                                    'size'],
                                                Heartbeat.challenge_type()
                                                .fromdict(
                                                    MockValues.
                                                    get_chunk_response['challe'
                                                                       'nge']),
                                                datetime.utcnow() +
                                                timedelta(
                                                    seconds=int(
                                                        MockValues.
                                                        get_chunk_response['du'
                                                                           'e']
                                                    )),
                                                Heartbeat.tag_type().fromdict(
                                                    MockValues
                                                    .get_chunk_response['ta'
                                                                        'g'],
                                                ),
                                                self.api)
        self.test_heartbeat = Heartbeat.fromdict(
            MockValues.connect_response['heartbeat'])

    def tearDown(self):
        pass

    def test_initialization(self):
        self.assertEqual(self.client.server, self.server_url.strip('/'))
        self.assertEqual(self.client.address, self.address)
        self.assertEqual(self.client.token, self.token)
        self.assertEqual(self.client.desired_size, self.size)
        self.assertIsNone(self.client.heartbeat)
        self.assertEqual(len(self.client.contracts), 0)

    def test_connect_no_token_no_address(self):
        self.client.address = None
        self.client.token = None
        with self.assertRaises(DownstreamError) as ex:
            self.client.connect()
        self.assertEqual(
            str(ex.exception), 'If no token is specified, address must be.')

    def test_connect_failed(self):
        with mock.patch('downstream_farmer.client.requests.get'),\
                mock.patch('downstream_farmer.client.handle_json_response')\
                as hp:
            hp.side_effect = DownstreamError('test error')
            with self.assertRaises(DownstreamError) as ex:
                self.client.connect()
            self.assertEqual(
                str(ex.exception), 'Unable to connect: test error')

    def test_connect_malformed(self):
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            inst = patch.return_value
            inst.json.return_value = {"invalid": "dict"}
            with self.assertRaises(DownstreamError) as ex:
                self.client.connect()
            self.assertEqual(
                str(ex.exception), 'Malformed response from server.')

    def test_connect_invalid_heartbeat(self):
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            inst = patch.return_value
            inst.json.return_value = {"heartbeat": "test heartbeat",
                                      "token": "test token",
                                      "type": "invalid type"}
            with self.assertRaises(DownstreamError) as ex:
                self.client.connect()
            self.assertEqual(str(ex.exception), 'Unknown Heartbeat Type')

    def test_connect_working_new(self):
        self.client.token = None
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            patch.return_value.json.return_value = MockValues.connect_response
            self.client.connect()
        patch.assert_called_with(
            '{0}/new/{1}'.format(self.server_url.strip('/') + self.api_path,
                                 self.address), verify=None)
        self.assertEqual(
            self.client.token, MockValues.connect_response['token'])
        self.assertEqual(self.client.heartbeat,
                         Heartbeat
                         .fromdict(MockValues.connect_response['heartbeat']))

    def test_connect_working(self):
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            patch.return_value.json.return_value = MockValues.connect_response
            self.client.connect()
        patch.assert_called_with('{0}/heartbeat/{1}'.format(
            self.server_url.strip('/') + self.api_path, self.token),
            verify=None)
        self.assertEqual(
            self.client.token, MockValues.connect_response['token'])
        self.assertEqual(self.client.heartbeat,
                         Heartbeat
                         .fromdict(MockValues.connect_response['heartbeat']))

    def test_connect_sign(self):
        self.client.msg = 'test message'
        self.client.sig = 'HyzVUenXXo4pa+kgm1vS8PNJM83eIXFC5r0q86FGbqFcdla6rcw'
        '72/ciXiEPfjli3ENfwWuESHhv6K9esI0dl5I='
        self.client.address = '19qVgG8C6eXwKMMyvVegsi3xCsKyk3Z3jV'
        self.client.token = None
        with mock.patch('downstream_farmer.client.requests.post') as patch:
            patch.return_value.json.return_value = MockValues.connect_response
            self.client.connect()
        patch.assert_called_with('{0}/new/{1}'.format(self.server_url
                                                      .strip('/') + self
                                                      .api_path, self
                                                      .client.address),
                                 data=json.dumps({
                                     "message": self.client.msg,
                                     "signature": self.client.sig
                                 }),
                                 headers={
                                     'Content-Type': 'application/json'
        },
            verify=None)
        self.assertEqual(
            self.client.token, MockValues.connect_response['token'])
        self.assertEqual(self.client.heartbeat,
                         Heartbeat.fromdict(MockValues
                                            .connect_response['heartbeat']))

    def test_get_chunk_no_token(self):
        with mock.patch('downstream_farmer.client.requests.get'),\
                mock.patch('downstream_farmer.client.handle_json_response')\
                as hp:
            hp.side_effect = DownstreamError('test error')
            with self.assertRaises(DownstreamError) as ex:
                self.client.get_chunk()
            self.assertEqual(
                str(ex.exception), 'Unable to get token: test error')

    def test_get_chunk_malformed(self):
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            patch.return_value.json.return_value = {"invalid": "dict"}
            with self.assertRaises(DownstreamError) as ex:
                self.client.get_chunk()
            self.assertEqual(
                str(ex.exception), 'Malformed response from server.')

    def test_get_chunk_working(self):
        self.client.heartbeat = self.test_heartbeat
        with mock.patch('downstream_farmer.client.requests.get') as patch:
            inst = patch.return_value
            inst.json.return_value = MockValues.get_chunk_response
            self.client.get_chunk()
        self.assertEqual(
            self.client.contracts[0].hash, self.test_contract.hash)
        self.assertEqual(
            self.client.contracts[0].seed, self.test_contract.seed)
        self.assertEqual(
            self.client.contracts[0].size, self.test_contract.size)
        self.assertEqual(
            self.client.contracts[0].challenge, self.test_contract.challenge)
        self.assertAlmostEqual((self.client.contracts[
                               0].expiration - self.test_contract.expiration)
                               .total_seconds(), 0, delta=1)
        self.assertEqual(self.client.contracts[0].tag, self.test_contract.tag)

    def test_get_total_size(self):
        client = mock.MagicMock(spec=DownstreamClient)
        contract1 = mock.MagicMock(spec=DownstreamContract)
        contract2 = mock.MagicMock(spec=DownstreamContract)
        contract1.size = 10
        contract2.size = 100
        client.contracts = [contract1, contract2]
        self.assertEqual(DownstreamClient.get_total_size(client), 110)
        client.contracts = list()
        self.assertEqual(DownstreamClient.get_total_size(client), 0)

    def test_get_next_contract(self):
        client = mock.MagicMock(spec=DownstreamClient)
        contract1 = mock.MagicMock(spec=DownstreamContract)
        contract2 = mock.MagicMock(spec=DownstreamContract)
        contract1.time_remaining.return_value = 10
        contract2.time_remaining.return_value = 100
        client.contracts = [contract1, contract2]
        self.assertEqual(contract1, DownstreamClient.get_next_contract(client))

    def test_run_obtain_contract_fail(self):
        client = mock.MagicMock(spec=DownstreamClient)
        client.get_total_size.return_value = 0
        client.desired_size = 100
        client.api = API()

        client.get_chunk.side_effect = DownstreamError('test error')
        client.contracts = []
        with self.assertRaises(DownstreamError) as ex:
            DownstreamClient.run(client, 1)
        self.assertEqual(
            str(ex.exception), 'Unable to obtain a contract: test error')

    def test_run_working(self):
        client = mock.MagicMock(spec=DownstreamClient)
        client.get_total_size.return_value = 0
        client.desired_size = 100
        client.api = API()

        contract = mock.MagicMock(spec=DownstreamContract)
        contract.time_remaining.return_value = 0
        contract.hash = '1'
        contract.api = client.api

        def patch_get_chunk(size):
            client.get_total_size.return_value = \
                client.get_total_size.return_value + \
                size
            client.contracts = [contract]

        client.get_chunk.side_effect = patch_get_chunk
        client.get_next_contract.return_value = contract
        DownstreamClient.run(client, 1)
        self.assertEqual(client.get_chunk.call_count, 1)
        self.assertEqual(contract.update_challenge.call_count, 1)
        self.assertEqual(contract.answer_challenge.call_count, 1)

    def test_run_working_block(self):
        client = mock.MagicMock(spec=DownstreamClient)
        client.get_total_size.return_value = 0
        client.desired_size = 100
        client.api = API()

        contract = mock.MagicMock(spec=DownstreamContract)
        contract.time_remaining.return_value = 1
        contract.hash = '1'
        contract.api = client.api

        def patch_get_chunk(size):
            client.get_total_size.return_value = \
                client.get_total_size.return_value + \
                size
            client.contracts = [contract]

        client.get_chunk.side_effect = patch_get_chunk
        client.get_next_contract.return_value = contract
        with mock.patch('time.sleep') as a:
            DownstreamClient.run(client, 1)
        self.assertEqual(client.get_chunk.call_count, 1)
        self.assertEqual(contract.update_challenge.call_count, 1)
        self.assertEqual(contract.answer_challenge.call_count, 1)
        self.assertEqual(a.call_count, 1)

    def test_run_update_failed(self):
        client = mock.MagicMock(spec=DownstreamClient)
        client.get_total_size.return_value = 100
        client.desired_size = 100
        client.api = API()

        contract = mock.MagicMock(spec=DownstreamContract)
        contract.time_remaining.return_value = 0
        contract.hash = '1'
        contract.api = client.api

        client.contracts = mock.MagicMock()
        client.contracts.remove = mock.MagicMock()

        contract.update_challenge.side_effect = DownstreamError('test error')
        client.get_next_contract.return_value = contract
        DownstreamClient.run(client, 1)
        self.assertTrue(client.contracts.remove.called)

    def test_run_answer_failed(self):
        client = mock.MagicMock(spec=DownstreamClient)
        client.get_total_size.return_value = 100
        client.desired_size = 100
        client.api = API()

        contract = mock.MagicMock(spec=DownstreamContract)
        contract.time_remaining.return_value = 0
        contract.hash = '1'
        contract.api = client.api

        client.contracts = mock.MagicMock()
        client.contracts.remove = mock.MagicMock()

        contract.answer_challenge.side_effect = DownstreamError('test error')
        client.get_next_contract.return_value = contract
        DownstreamClient.run(client, 1)
        self.assertTrue(client.contracts.remove.called)


class TestExceptions(unittest.TestCase):

    def test_downstream_error(self):
        e = DownstreamError('Test Exception')
        self.assertEqual(str(e), 'Test Exception')


class MockRestore(object):

    def __init__(self, table):
        self.table = table

    def __call__(self, arg):
        return self.table[arg]


class MockRaiseOnFirstCall(object):

    def __init__(self, error):
        self.called = False
        self.error = error

    def __call__(self, arg=None):
        if (not self.called):
            self.called = True
            raise self.error


class TestShell(unittest.TestCase):

    def setUp(self):
        self._old_argv = sys.argv
        sys.argv = [
            'downstream'
        ]
        self.test_args = mock.MagicMock()
        self.test_args.number = None
        self.test_args.node_url = 'http://testurl/'
        self.test_args.api_path = '/api/downstream/v1'
        self.test_args.token = 'testtoken'
        self.test_args.address = 'testaddress'
        self.test_args.size = 100
        self.test_args.history = 'historyfile'
        self.test_args.forcenew = False
        self.test_args.identity = 'identityfile'
        self.test_args.api = API()

    def tearDown(self):
        sys.argv = self._old_argv

    def test_fail_exit(self):
        with self.assertRaises(SystemExit):
            shell.fail_exit('Test')

    def test_handler(self):
        with self.assertRaises(SystemExit):
            shell.handler()

    def test_api_start(self):
        api = API()
        api.start()
        self.assertEqual(api.running, 1)

    def test_api_stop(self):
        api = API()
        api.stop()
        self.assertEqual(api.running, 0)

    def test_api_plus_heartbeats(self):
        api = API()
        api.plus_heartbeats(1)
        self.assertEqual(api.heartbeats, 1)

    def test_api_retrieve_heartbeats(self):
        api = API()
        self.assertEqual(api.heartbeats, 0)

    def test_api_plus_contracts(self):
        api = API()
        api.plus_contracts(1)
        self.assertEqual(api.contracts, 1)

    def test_api_retrieve_contracts(self):
        api = API()
        self.assertEqual(api.contracts, 0)

    def test_farmer_init_number_invalid(self):
        self.test_args.number = -1
        with self.assertRaises(DownstreamError) as ex:
            Farmer(self.test_args)
        self.assertEqual(
            str(ex.exception), 'Must specify a positive number of challenges.')

    def test_farmer_init_size_invalid(self):
        self.test_args.size = 0
        with self.assertRaises(DownstreamError) as ex:
            Farmer(self.test_args)
        self.assertEqual(
            str(ex.exception), 'Must specify a positive size to farm.')

    def test_farmer_init_forcenew(self):
        self.test_args.forcenew = True
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
        self.assertIsNone(farmer.token)

    def test_farmer_init_no_token_no_address(self):
        self.test_args.token = None
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'),\
                self.assertRaises(DownstreamError) as ex:
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            Farmer(self.test_args)
        self.assertEqual(
            str(ex.exception),
            'Must specify farming address if one is not available.')

    def test_farmer_init_url(self):
        self.test_args.node_url = 'testurl'
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.url, self.test_args.node_url)
            self.assertEqual(
                farmer.state['last_node'], self.test_args.node_url)

    def test_farmer_init_url_from_state(self):
        self.test_args.node_url = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': {'last_node': 'stateurl'},
                 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.url, 'stateurl')

    def test_farmer_init_url_default(self):
        self.test_args.node_url = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.url, 'https://live.driveshare.org:8443')

    def test_farmer_init_token(self):
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.token, self.test_args.token)

    def test_farmer_init_token_from_state(self):
        self.test_args.token = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore({
                'historyfile': {
                    'nodes': {
                        self.test_args.node_url.strip('/'): {
                            'token': 'statetoken',
                            'address': 'testaddress'
                        }
                    }
                },
                'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.token, 'statetoken')

    def test_farmer_init_token_default(self):
        self.test_args.token = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.token, None)

    def test_farmer_init_address(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.address, self.test_args.address)

    def test_farmer_init_address_from_state(self):
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore({'historyfile': {
                'nodes': {
                    self.test_args.node_url.strip('/'): {
                        'token': 'statetoken',
                        'address': 'stateaddress'
                    }
                }
            }, 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.address, 'stateaddress')

    def test_farmer_init_address_default(self):
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.address, None)

    def test_farmer_init_address_from_identities(self):
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore({'historyfile': dict(),
                                         'identityfile':
                                         {'19qVgG8C6eXwKMMyvVegsi3xCsKyk3Z3jV':
                                          {'signature': 'HyzVUenXXo4pa+kgm1v'
                                           'S8PNJM83eIXFC5r0q86FGbqFcdla6rcw'
                                           '72/ciXiEPfjli3ENfwWuESHhv6K9esI0'
                                           'dl5I=', 'message':
                                           'test message'}}})
            farmer = Farmer(self.test_args)
            self.assertEqual(
                farmer.address, '19qVgG8C6eXwKMMyvVegsi3xCsKyk3Z3jV')

    def test_farmer_load_signature_invalid_dict(self):
        self.test_args.token = None
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'):
            r.side_effect = MockRestore({'historyfile': dict(),
                                         'identityfile':
                                         {'identityaddress': {'invalid':
                                                              'dict'}}})
            with self.assertRaises(DownstreamError) as ex:
                Farmer(self.test_args)
            self.assertEqual(str(ex.exception),
                             'The file format for the identity file '
                             '{0} should be a JSON formatted dictionary like '
                             'the following:\n'
                             '   {{\n'
                             '      "your sjcx address": {{\n'
                             '         "message": "your message here",\n'
                             '         "signature":  "base64 signature from '
                             'bitcoin wallet or counterwallet",\n'
                             '      }}\n'
                             '   }}'.format(self.test_args.identity))

    def test_farmer_load_signature_invalid_sig(self):
        self.test_args.token = None
        self.test_args.address = None
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(Farmer, 'check_connectivity'),\
                mock.patch(
                    'siggy.verify_signature') as s:
            s.return_value = False
            r.side_effect = MockRestore({'historyfile': dict(),
                                         'identityfile':
                                         {'identityaddress':
                                          {'signature': 'testsig', 'message':
                                           'testmessage'}}})
            with self.assertRaises(DownstreamError) as ex:
                Farmer(self.test_args)
            self.assertEqual(str(ex.exception), 'Signature provided does not'
                             ' match address being used. '
                             'Check your formatting, your SJCX address, and'
                             ' try again.')

    def test_farmer_load_signature_none(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch.object(
                    Farmer, 'check_connectivity'):
            r.side_effect = MockRestore({'historyfile': dict(),
                                         'identityfile':
                                         {'identityaddress':
                                          {'signature': 'testsig', 'message':
                                           'testmessage'}}})
            farmer = Farmer(self.test_args)
            self.assertEqual(farmer.message, '')
            self.assertEqual(farmer.signature, '')

    def test_farmer_save_restore_state(self):
        d = {'key': 'value'}
        path = 'test_file'
        with mock.patch.object(Farmer, '__init__', autospec=True) as i:
            i.return_value = None
            farmer = Farmer(None)
        farmer.path = path
        farmer.state = d
        save(farmer.path, farmer.state)
        farmer.state = restore(farmer.path)
        self.assertEqual(d, farmer.state)
        os.remove(path)

        # test path doesn't exist
        farmer.path = 'nonexistentpath'
        farmer.state = restore(farmer.path)
        self.assertEqual(farmer.state, dict())

        # test directory creation
        dir = 'testdir'
        path = os.path.join(dir, 'file')
        farmer.path = path
        farmer.state = d
        save(farmer.path, farmer.state)
        self.assertTrue(os.path.isdir(dir))
        self.assertTrue(os.path.exists(path))
        farmer.state = restore(farmer.path)
        self.assertEqual(d, farmer.state)
        os.remove(path)
        os.rmdir(dir)

        # test parse fail
        path = 'test_file'
        with open(path, 'w') as f:
            f.write('test contents')
        with mock.patch('json.loads') as l:
            l.side_effect = Exception('test error')
            with self.assertRaises(DownstreamError) as ex:
                restore(path)
            self.assertEqual(
                str(ex.exception), 'Couldn\'t parse \'{0}\': test error'
                .format(path))
        os.remove(path)

    def test_farmer_check_connectivity(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch(
                    'six.moves.urllib.request.urlopen') as patch:
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            patch.side_effect = URLError('Problem')
            with self.assertRaises(DownstreamError):
                farmer.check_connectivity()

        with mock.patch('six.moves.urllib.request.urlopen') as patch:
            farmer.check_connectivity()
            self.assertTrue(patch.called)

    def test_farmer_run(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch(
                    'six.moves.urllib.request.urlopen') as patch:
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
            farmer.api = API()
        with mock.patch(
            'downstream_farmer.shell.DownstreamClient') as patch,\
                mock.patch(
                    'downstream_farmer.shell.save', autospec=True) as s:
            patch.return_value.token = 'foo'
            patch.return_value.address = 'bar'
            farmer.run()
            patch.assert_called_with(
                farmer.url, farmer.token, farmer.address, farmer.size, '', '',
                farmer.api)
            self.assertTrue(patch.return_value.connect.called)
            self.assertEqual(farmer
                             .state['nodes'][patch.return_value
                                             .server]['token'],
                             patch.return_value.token)
            self.assertEqual(farmer
                             .state['nodes'][patch.return_value
                                             .server]['address'],
                             patch.return_value.address)
            self.assertTrue(s.called)
            patch.return_value.run.assert_called_with(farmer.number)

    def test_farmer_run_no_reconnect(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch(
                    'six.moves.urllib.request.urlopen') as patch:
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
        with mock.patch(
            'downstream_farmer.shell.DownstreamClient') as patch,\
                mock.patch(
                    'downstream_farmer.shell.save', autospec=True):
            patch.return_value.run.side_effect = MockRaiseOnFirstCall(
                DownstreamError('first error'))
            with self.assertRaises(DownstreamError) as ex:
                farmer.run()
            self.assertEqual(str(ex.exception), 'first error')

    def test_farmer_run_reconnect(self):
        with mock.patch(
            'downstream_farmer.shell.restore', autospec=True) as r,\
                mock.patch(
                    'six.moves.urllib.request.urlopen') as patch:
            r.side_effect = MockRestore(
                {'historyfile': dict(), 'identityfile': dict()})
            farmer = Farmer(self.test_args)
        with mock.patch('downstream_farmer.shell.DownstreamClient') as patch,\
                mock.patch('downstream_farmer.shell.save', autospec=True),\
                mock.patch('time.sleep') as t:
            patch.return_value.run.side_effect = MockRaiseOnFirstCall(
                DownstreamError('first error'))
            farmer.run(reconnect=True)
            t.assert_called_with(10)

    def test_eval_args_run(self):
        with mock.patch('downstream_farmer.shell.Farmer') as farmer:
            shell.eval_args(mock.MagicMock())
        self.assertTrue(farmer.called)
        self.assertTrue(farmer.return_value.run.called)

    def test_eval_args_downstream_error(self):
        with mock.patch('downstream_farmer.shell.Farmer') as farmer:
            farmer.side_effect = DownstreamError('error')
            with self.assertRaises(SystemExit):
                shell.eval_args(None)

    def test_eval_args_exception(self):
        with mock.patch('downstream_farmer.shell.Farmer') as farmer:
            farmer.side_effect = Exception('error')
            with self.assertRaises(SystemExit):
                shell.eval_args(None)

    def test_eval_args_catchall(self):
        with mock.patch('downstream_farmer.shell.Farmer') as farmer:
            farmer.side_effect = BaseException('error')
            with self.assertRaises(SystemExit):
                shell.eval_args(None)

    def test_parse_args(self):
        args = shell.parse_args()
        self.assertIsInstance(args, Namespace)

    def test_parse_args_version(self):
        with self.assertRaises(SystemExit):
            sys.argv.append('--version')
            shell.parse_args()

    def test_parse_args_number(self):
        sys.argv.append('--number')
        sys.argv.append('1')
        args = shell.parse_args()
        self.assertEqual(args.number, int(sys.argv[2]))

    def test_parse_args_number_default(self):
        args = shell.parse_args()
        self.assertEqual(args.number, None)

    def test_parse_args_path(self):
        sys.argv.append('--history')
        sys.argv.append('testpath')
        args = shell.parse_args()
        self.assertEqual(args.history, sys.argv[2])

    def test_parse_args_history_default(self):
        args = shell.parse_args()
        self.assertEqual(args.history, os.path.join('data', 'history.json'))

    def test_parse_args_size(self):
        sys.argv.append('--size')
        sys.argv.append('10')
        args = shell.parse_args()
        self.assertEqual(args.size, int(sys.argv[2]))

    def test_parse_args_size_default(self):
        args = shell.parse_args()
        self.assertEqual(args.size, 100)

    def test_parse_args_address(self):
        sys.argv.append('--address')
        sys.argv.append('testaddress')
        args = shell.parse_args()
        self.assertEqual(args.address, sys.argv[2])

    def test_parse_args_address_default(self):
        args = shell.parse_args()
        self.assertEqual(args.address, None)

    def test_parse_args_token(self):
        sys.argv.append('--token')
        sys.argv.append('testtoken')
        args = shell.parse_args()
        self.assertEqual(args.token, sys.argv[2])

    def test_parse_args_token_default(self):
        args = shell.parse_args()
        self.assertEqual(args.token, None)

    def test_parse_args_url(self):
        sys.argv.append('testurl')
        args = shell.parse_args()
        self.assertEqual(args.node_url, sys.argv[1])

    def test_parse_args_url_default(self):
        args = shell.parse_args()
        self.assertEqual(args.node_url, None)

    def test_main(self):
        with mock.patch('downstream_farmer.shell.parse_args') as pa:
            with mock.patch('downstream_farmer.shell.eval_args') as ea:
                shell.main()
                self.assertTrue(pa.called)
                self.assertTrue(ea.called)


class MockValues:
    connect_response = {
        "heartbeat": "AQoAAACAAAAAgAAAAJCTCchnuw8nE9FbjUyJVNNzjQumBHHw7iFL5Ply"
                     "4vHQvkqOqcgc5XKXgWVaJGCs1F+oI68zL9Ir9+q0BkA5WadDq5uz0Cot"
                     "sY8Pad8UemCLvLGNlnkavsbn0dXk7/0QL5KYGardu9m5zWtQEagdvl86"
                     "tSbksec1B5Y9K1S5hGlr",
        "token": "b45a3e2932c87474cb1bd7e642cf792b",
        "type": "Swizzle"
    }

    get_chunk_response = {
        "challenge": "AQAAACAAAACJwjEuYPkbnGOppNVgG0Xc5GKgp0g2kGN2bMCssbMBwIAA"
                     "AACQkwnIZ7sPJxPRW41MiVTTc40LpgRx8O4hS+T5cuLx0L5KjqnIHOVy"
                     "l4FlWiRgrNRfqCOvMy/SK/fqtAZAOVmnQ6ubs9AqLbGPD2nfFHpgi7yx"
                     "jZZ5Gr7G59HV5O/9EC+SmBmq3bvZuc1rUBGoHb5fOrUm5LHnNQeWPStU"
                     "uYRpaw==",
        "due": "60",
        "file_hash": "89ca8e5f02e64694bf889d49a9b7986f201900e6637e0e7349282a85"
                     "91ce7732",
        "seed": "eb1bb0f7cd24720d456193cca8c42edb",
        "size": 100,
        "tag": "AQAAAIAAAABqXU8BK1mOXFG0mK+X1lWNZ39AmYe1M4JsbIz36wC0PvvcWY+URw"
               "+BYBlFk5N1+X5VI4F+3sDYYy0jE7mgVCh7kNnOZ/mAYtffFh7izOOS4HHuzWIm"
               "cOgaVeBL0/ngSPLPYUhFF5uTzKoYUr+SheQDYcuOCg8qivXZGOL6Hv1WVQ=="
    }

    get_challenge_response = {
        "challenge": "AQAAACAAAAAs/0pRrQ00cWS86II/eAufStyqrjf0wSJ941EjtrLo94AA"
                     "AABSnAK49Tm7F/4HkQuvdJj1WdisL9OEuMMl9uYMxIp8aXvDqkI/NP4r"
                     "ix6rREa1Jh6pvH6Mb4DpVHEjDMzVIOKEKV8USKndUq2aNiYf2NqQ1Iw0"
                     "XkNFsoSgZD10miN8YtatUNu+8gUkT6cv54DUrruo9JTIpXsIqu0BNifu"
                     "FU58Vw==",
        "due": "60",
        "answered": True
    }
