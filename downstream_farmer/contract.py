import io
import json
import time

from datetime import datetime, timedelta
import requests
from RandomIO import RandomIO

from .utils import handle_json_response
from .exc import DownstreamError

heartbeats = 0

class DownstreamContract(object):

    def __init__(self, client, hash, seed, size, challenge, expiration, tag):
        self.hash = hash
        self.seed = seed
        self.size = size
        self.challenge = challenge
        self.expiration = expiration
        self.tag = tag
        self.client = client
        self.answered = False
        self.cert_path = None

    def set_cert_path(self, cert_path):
        """Sets the path of a CA-Bundle to use for verifying requests
        """
        self.cert_path = cert_path

    def time_remaining(self):
        """Returns the amount of time until this challenge
        is ready to be updated.

        :returns: time til expiration in seconds
        """
        if (self.answered):
            time_til_expiration = self.expiration - datetime.utcnow()
            return time_til_expiration.total_seconds()
        else:
            return 0

    def update_challenge(self, block=True):
        """Updates the challenge for this contract

        Checks that existing challenge has expired before getting a new one
        :param block: if block is True, waits until the old challenge has
        expired before getting a new one.  Otherwise, if the old challenge
        has not expired, returns immediately
        """
        if (not self.answered):
            # dont need to update since we haven't answered yet.
            return

        time_til_expiration = self.time_remaining()
        if (time_til_expiration > 0):
            if (block):
                print('Waiting {0} seconds until new challenge is available.'
                      .format(time_til_expiration))
                # contract expiration is in the future...
                # wait til contract expiration
                time.sleep(time_til_expiration)
            else:
                return

        # now contract should be expired, we can get a new challenge

        url = '{0}/challenge/{1}/{2}'.format(self.client.api_url,
                                             self.client.token,
                                             self.hash)
        try:
            resp = requests.get(url, verify=self.cert_path)
        except:
            raise DownstreamError('Unable to perform HTTP get.')

        try:
            r_json = handle_json_response(resp)
        except DownstreamError:
            raise DownstreamError('Challenge update failed.')

        for k in ['challenge', 'due', 'answered']:
            if (k not in r_json):
                raise DownstreamError('Malformed response from server.')

        self.challenge = self.client.heartbeat.challenge_type().\
            fromdict(r_json['challenge'])
        self.expiration = datetime.utcnow()\
            + timedelta(seconds=int(r_json['due']))
        self.answered = r_json['answered']

    def answer_challenge(self):
        """Answers the challenge.
        """
        if (self.answered):
            # we don't answer challenges that have already been answered
            # there isn't any point
            return

        url = '{0}/answer/{1}/{2}'.format(self.client.api_url,
                                          self.client.token,
                                          self.hash)

        with io.BytesIO(RandomIO(self.seed).read(self.size)) as f:
            proof = self.client.heartbeat.prove(f, self.challenge, self.tag)

        data = {
            'proof': proof.todict()
        }
        headers = {
            'Content-Type': 'application/json'
        }

        try:
            resp = requests.post(url,
                                 data=json.dumps(data),
                                 headers=headers,
                                 verify=self.cert_path)
        except:
            raise DownstreamError('Unable to perform HTTP post.')

        try:
            r_json = handle_json_response(resp)
        except DownstreamError as ex:
            raise DownstreamError(
                'Challenge answer failed: {0}'.format(str(ex)))

        if ('status' not in r_json):
            raise DownstreamError('Malformed response from server.')

        if (r_json['status'] != 'ok'):
            raise DownstreamError('Challenge response rejected.')

        self.answered = True
        
        global heartbeats
        heartbeats += 1
