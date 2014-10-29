#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import time
import binascii
import hashlib

import requests
import heartbeat
from datetime import datetime, timedelta

from .utils import handle_json_response
from .exc import DownstreamError
from .contract import DownstreamContract

heartbeat_types = {'Swizzle': heartbeat.Swizzle.Swizzle,
                   'Merkle': heartbeat.Merkle.Merkle}


class DownstreamClient(object):

    def __init__(self, url, token, address, size):
        self.server = url.strip('/')
        self.token = token
        self.address = address
        self.desired_size = size
        self.heartbeat = None
        self.contracts = list()

    def connect(self):
        """Connects to a downstream-node server.
        """
        if (self.token is None):
            if (self.address is None):
                raise DownstreamError(
                    'If no token is specified, address must be.')
            # get a new token
            url = '{0}/api/downstream/new/{1}'.\
                format(self.server, self.address)
        else:
            # try to use our token
            url = '{0}/api/downstream/heartbeat/{1}'.\
                format(self.server, self.token)

        resp = requests.get(url)

        try:
            r_json = handle_json_response(resp)
        except DownstreamError as ex:
            # can't handle a connect error.  fail.
            raise DownstreamError('Unable to connect: {0}'.
                                  format(str(ex)))

        for k in ['token', 'heartbeat', 'type']:
            if (k not in r_json):
                raise DownstreamError('Malformed response from server.')

        if r_json['type'] not in heartbeat_types.keys():
            raise DownstreamError('Unknown Heartbeat Type')

        self.token = r_json['token']
        self.heartbeat \
            = heartbeat_types[r_json['type']].fromdict(r_json['heartbeat'])

        # we can calculate farmer id for display...
        token = binascii.unhexlify(self.token)
        token_hash = hashlib.sha256(token).hexdigest()[:20]
        print('Obtained token: {0}'.format(self.token))
        print('Farmer id: {0}'.format(token_hash))

    def get_chunk(self, size=None):
        """Gets a chunk contract from the connected node

        :param size: the maximum size of the contract, not yet used
        """
        url = '{0}/api/downstream/chunk/{1}'.format(self.server, self.token)

        resp = requests.get(url)

        try:
            r_json = handle_json_response(resp)
        except DownstreamError as ex:
            # can't handle an invalid token
            raise DownstreamError('Unable to get token: {0}'.
                                  format(str(ex)))

        for k in ['file_hash', 'seed', 'size', 'challenge', 'tag', 'due']:
            if (k not in r_json):
                raise DownstreamError('Malformed response from server.')

        contract = DownstreamContract(
            self,
            r_json['file_hash'],
            r_json['seed'],
            r_json['size'],
            self.heartbeat.challenge_type().fromdict(r_json['challenge']),
            datetime.utcnow() + timedelta(seconds=int(r_json['due'])),
            self.heartbeat.tag_type().fromdict(r_json['tag']))

        self.contracts.append(contract)

        print('Got chunk contract for file hash {0}'.format(contract.hash))

        print('Total size now {0}'.format(self.get_total_size()))

    def get_total_size(self):
        """Returns the total size of all the current contracts
        """
        if (len(self.contracts) > 0):
            return sum(c.size for c in self.contracts)
        else:
            return 0

    def get_next_contract(self):
        """Finds the next contract to update and answer based on
        time til expiration
        """
        next_contract = None
        least_time = None
        for c in self.contracts:
            time_on_this_contract = c.time_remaining()
            if (least_time is None or time_on_this_contract < least_time):
                next_contract = c
                least_time = time_on_this_contract
        return next_contract

    def run(self, number):
        """Updates and answers challenges for all contracts.
        """
        i = 0
        while (number is None or i < number):
            i += 1
            # ensure that we have contracts
            try:
                while (self.get_total_size() < self.desired_size):
                    print('Total size: {0}, Desired Size: {1}'.
                          format(self.get_total_size(), self.desired_size))
                    size_to_fill = self.desired_size - self.get_total_size()
                    print('{0} bytes remaining'.format(size_to_fill))
                    self.get_chunk(size_to_fill)
            except DownstreamError:
                # probably no more contracts. if we don't have any, raise error
                if (len(self.contracts) == 0):
                    raise DownstreamError('Unable to obtain a contract.')
                # otherwise, continue, since he have one

            # get the next expiring contract
            next_contract = self.get_next_contract()

            # sleep until the contract is ready
            time_to_wait = next_contract.time_remaining()

            if (time_to_wait > 0):
                print('Sleeping {0}'.format(time_to_wait))
                time.sleep(time_to_wait)

            # update the challenge.  don't block if for any reason
            # we would (which we shouldn't anyway)
            # print('Updating contract {0}'.format(next_contract.hash))
            try:
                next_contract.update_challenge(False)
            except DownstreamError as ex:
                # challenge update failed, delete this contract
                print('Challenge update failed: {0}\nDropping contract {1}'.
                      format(str(ex), next_contract.hash))
                self.contracts.remove(next_contract)
                continue

            # answer the challenge
            print('Answering challenge.')
            try:
                next_contract.answer_challenge()
            except DownstreamError as ex:
                # challenge answer failed, remove this contract
                print('Challenge answer failed: {0}, dropping contract {1}'.
                      format(str(ex), next_contract.hash))
                self.contracts.remove(next_contract)
                continue
