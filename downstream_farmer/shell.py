#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function

import os
import sys
import argparse
import json
import time
import siggy

from .client import DownstreamClient
from .version import __version__
from .exc import DownstreamError
from .utils import resource_path
from os.path import expanduser

import six, shutil, platform


class SmartFormatter(argparse.HelpFormatter):

    """From http://stackoverflow.com/questions/3853722/python-argparse-how-to-insert-newline-in-the-help-text  # NOQA
    """

    def _split_lines(self, text, width):
        # this is the RawTextHelpFormatter._split_lines
        if text.startswith('R|'):
            return text[2:].splitlines()
        return argparse.HelpFormatter._split_lines(self, text, width)


def fail_exit(msg, exit_code=1):
    sys.stderr.write('%s\n' % msg)
    sys.exit(exit_code)


def handler(signum=None, frame=None):
    sys.exit(0)


def save(path, obj):
    """saves the farmer state to disk

    :param path: the path to save to
    :param obj: the object to save (must be json serializable)
    """
    (head, tail) = os.path.split(path)
    if (len(head) > 0 and not os.path.isdir(head)):
        os.mkdir(head)
    with open(path, 'w+') as f:
        json.dump(obj, f)


def restore(path):
    """restores state from disk

    :param path: the path to restore from
    :returns: the object restored, or an empty dict(), if the file doesn't
        exist
    """
    if (os.path.exists(path)):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as ex:
            raise DownstreamError(
                'Couldn\'t parse \'{0}\': {1}'.format(path, str(ex)))
    else:
        return dict()


class Farmer(object):

    def __init__(self, args):
        """The farmer should have some priorities on how it uses the
        parameters.

        1) if a url is not specified, it loads saved url and connects to that
           node
        2) if no node is specified on disk,  connect to our prototype node.
        3) if no token is specified, it attempts to load the token for the node
           from disk
        4) if no token is on disk, it will attempt to retrieve a new farming
           token from the node.  this requires an address.
        5) if no address is specified on the command line, it will attempt to
           load the address for the node from the identities file
        6) if no address is available, fail.
        7) if an address is given on the command line that is different from
           the saved address, it uses the specified address and obtains a new
           token

        :param args: the arguments from the command line
        """

        self.cert_path = resource_path('ca-bundle.crt')

        self.load_number(args)

        self.load_size(args)

        # restore history and identities from file, if possible
        self.history_path = args.history
        self.identity_path = args.identity

        self.state = restore(self.history_path)
        self.identities = restore(self.identity_path)

        self.load_url_and_check(args)

        self.load_token(args)

        self.load_address(args)

        self.load_signature(args)

        if (self.token is None and self.address is None):
            raise DownstreamError(
                'Must specify farming address if one is not available.')

        if (self.token is not None):
            print('Using token {0}'.format(self.token))

        if (self.address is not None):
            print('Farming on address {0}'.format(self.address))\


    def load_number(self, args):
        """Loads the number of challenges from the command line
        """
        if args.number is not None and args.number < 1:
            raise DownstreamError(
                'Must specify a positive number of challenges.')

        self.number = args.number

    def load_size(self, args):
        """Loads the total farming size from the command line
        """
        if args.size < 1:
            raise DownstreamError('Must specify a positive size to farm.')

        self.size = args.size

    def load_url_and_check(self, args):
        """Loads the target node url from the command line, or from the last
        known node, or the default.  Also checks connectivity to the node.
        """
        if (args.node_url is None):
            if ('last_node' in self.state):
                url = self.state['last_node']
            else:
                url = 'https://live.driveshare.org:8443'
        else:
            url = args.node_url

        self.url = url.strip('/')
        print('Using url {0}'.format(self.url))

        self.check_connectivity()

        self.state['last_node'] = self.url

    def load_token(self, args):
        """Either loads a saved token from history, from command line
        or, sets token to None if a new token is needed
        """
        saved_token = self.state.get('nodes', dict()).\
            get(self.url, dict()).get('token', None)

        if (args.token is not None):
            self.token = args.token
        else:
            self.token = saved_token

        if (args.forcenew):
            if (self.token is not None):
                print('Not using token {0} since '
                      'forcenew was specified.'.format(self.token))
                self.token = None

    def load_address(self, args):
        """Loads SJCX address either from history, command line, or from
        identities file
        """
        saved_address = self.state.get('nodes', dict()).\
            get(self.url, dict()).get('address', None)

        if (args.address is not None):
            self.address = args.address
            if (self.address != saved_address):
                print('New address specified, obtaining new token.')
                self.token = None
        else:
            self.address = saved_address

        if (self.address is None):
            # no address specified on command line or in history with this
            # node, let's get one from the identities file if we can!
            if (len(self.identities) > 0):
                # we have at least one identity...
                # just take the first one
                self.address = next(iter(self.identities))

    def load_signature(self, args):
        """Loads a signature from the identities file for the address
        we are going to use.  If one is not specified, throws an error.
        """
        if (self.address in self.identities):
            # get the signatures associated with the identity
            if ('message' not in self.identities[self.address] or
                    'signature' not in self.identities[self.address]):
                raise DownstreamError(
                    'The file format for the identity file '
                    '{0} should be a JSON formatted dictionary like the '
                    'following:\n'
                    '   {{\n'
                    '      "your sjcx address": {{\n'
                    '         "message": "your message here",\n'
                    '         "signature":  "base64 signature from bitcoin '
                    'wallet or counterparty",\n'
                    '      }}\n'
                    '   }}'.format(self.identity_path))
            self.message = self.identities[self.address]['message']
            self.signature = self.identities[self.address]['signature']
            if (not siggy.verify_signature(self.message,
                                           self.signature,
                                           self.address)):
                raise DownstreamError(
                    'Signature provided does not match address being used. '
                    'Check your formatting, your SJCX address, and try again.')
        else:
            # the address being used does not have any associated signatures
            # we will attempt to connect without them
            self.message = ''
            self.signature = ''

    def check_connectivity(self):
        """ Check to see if we even get a connection to the server.
        https://stackoverflow.com/questions/3764291/checking-network-connection
        """
        try:
            six.moves.urllib.request.urlopen(self.url, timeout=2)
        except six.moves.urllib.error.URLError:
            raise DownstreamError("Could not connect to server.")

    def run(self, reconnect=False):
        client = DownstreamClient(
            self.url, self.token, self.address,
            self.size, self.message, self.signature)

        client.set_cert_path(self.cert_path)

        while (1):
            try:
                client.connect()

                # connection successful, save our state, then begin farming
                self.state.setdefault('nodes', dict())[client.server] = {
                    'token': client.token,
                    'address': client.address
                }

                save(self.history_path, self.state)

                client.run(self.number)

                # client finished without an error
                break
            except Exception as ex:
                # check if this is a token issue...
                if (type(ex) is DownstreamError):
                    if (str(ex) == 'Unable to connect: Nonexistent token.'):
                        # token didn't exist on the server... clear token
                        # and try again
                        print('Given token did not exist on remote server. '
                              'Attempting to obtain a new token.')
                        client.token = None
                        continue
                if (not reconnect):
                    raise
                else:
                    print(str(ex))
                    print('Reconnecting in 10 seconds...')
                    time.sleep(10)


def eval_args(args):
    try:
        farmer = Farmer(args)

        farmer.run(args.keepalive)

    except DownstreamError as e:
        fail_exit('Error: {0}'.format(str(e)))
    except Exception as e:
        fail_exit('Unexpected error: {0}'.format(str(e)))
    except:
        fail_exit('Unknown error.')


def parse_args():
    drivesharedir = None

    os_type = platform.system()
    if os_type == "Windows":
        appdata = os.getenv('APPDATA')
        drivesharedir = os.path.realpath("%s/DriveShare" % appdata)
        if not os.path.exists(drivesharedir):
            os.mkdir(drivesharedir)
    elif os_type == "Darwin":
        appdata = expanduser("~/Application Support")
        drivesharedir = os.path.realpath("%s/DriveShare" % appdata)
        if not os.path.exists(drivesharedir):
            os.mkdir(drivesharedir)
    else:
        appdata = expanduser("~")
        drivesharedir = os.path.realpath("%s/.driveshare" % appdata)
        if not os.path.exists(drivesharedir):
            os.mkdir(drivesharedir)

    identity_path = os.path.join(drivesharedir, 'identities.json')

    if not os.path.exists(identity_path):
        shutil.copy2(os.path.join('data', 'identities.json'), identity_path)

    if not os.path.exists(os.path.join(drivesharedir, 'settings.json')):
        shutil.copy2(os.path.join('data', 'settings.json'), os.path.join(drivesharedir, 'settings.json'))

    with open(os.path.join(drivesharedir, 'settings.json')) as f:
        history_path = os.path.join(json.loads(f.read())["path"], 'history.json')

    print(history_path)

    default_size = 100
    default_url = 'https://live.driveshare.org:8443'
    parser = argparse.ArgumentParser(
        'downstream', formatter_class=SmartFormatter)
    parser.add_argument('-V', '--version', action='version',
                        version=__version__)
    parser.add_argument('node_url', nargs='?',
                        help='URL of the downstream node to connect to. '
                        'The default node is {0}'.format(default_url))
    parser.add_argument('-n', '--number', type=int,
                        help='Number of challenges to perform. '
                        'If unspecified, perform challenges until killed.')
    parser.add_argument('-p', '--history', default=history_path,
                        help='Path to save/load history from. The history file'
                        ' saves your farming tokens for each node you connect '
                        'to.  The default path is {0}.'.format(history_path))
    parser.add_argument('-s', '--size', type=int, default=default_size,
                        help='Total size of contracts to obtain in bytes. '
                        'Default is {0} bytes'.format(default_size))
    parser.add_argument('-a', '--address', help='SJCX address for farming. You'
                        ' can specify this if you have multiple identities and'
                        ' would like to farm under one of them.  Otherwise by '
                        'default, an address from your identity file ({0}) '
                        'will be used.'.format(identity_path))
    parser.add_argument('-t', '--token', help='Farming token to use.  If you '
                        'already have a farming token, you can reconnect to '
                        'the node with it by specifying it here.  By default '
                        'a new token will be obtained.  Any tokens obtained '
                        'will be saved in the history JSON file.')
    parser.add_argument('-f', '--forcenew', help='Force obtaining a new token.'
                        ' If the node has been reset and your token has been '
                        'deleted, it may be necessary to force your farmer to '
                        'obtain a new token.',
                        action='store_true')
    parser.add_argument('-i', '--identity', default=identity_path,
                        help='R|Specify an identity file to  provide a '
                        'signature to\nprove ownership of your SJCX address. '
                        'The default path\nis {0}.  The file format should be '
                        'a\nJSON dictionary like the following:\n'
                        '{{\n'
                        '   "your sjcx address": {{\n'
                        '      "message": "your message here",\n'
                        '      "signature": "base64 signature from bitcoin\\\n'
                        '                     wallet or counterparty",\n'
                        '   }}\n'
                        '}}\n'
                        'If an identity is specified in this file, it will '
                        'be\nused for connecting to any new nodes.'
                        .format(identity_path))
    parser.add_argument('-k', '--keepalive', help='Will attempt to reconnect '
                        'upon failure.', action='store_true')
    return parser.parse_args()


def main():
    args = parse_args()
    eval_args(args)
