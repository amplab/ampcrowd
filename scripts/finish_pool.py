#!/usr/bin/env python
import httplib
import json
import logging
import operator
import socket
import ssl
import sys
import urllib
import urllib2
import uuid

from argparse import ArgumentParser

logging.getLogger().setLevel(logging.INFO)

# custom HTTPS opener, django-sslserver supports SSLv3 only
class HTTPSConnectionV3(httplib.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        httplib.HTTPSConnection.__init__(self, *args, **kwargs)

    def connect(self):
        sock = socket.create_connection((self.host, self.port), self.timeout)
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
        try:
            self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file, ssl_version=ssl.PROTOCOL_TLSv1)
        except ssl.SSLError as e:
            print("Trying SSLv3.")
            self.sock = ssl.wrap_socket(sock, self.key_file, self.cert_file, ssl_version=ssl.PROTOCOL_TLSv1)

class HTTPSHandlerV3(urllib2.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(HTTPSConnectionV3, req)

def send_request(data, crowd, use_ssl):
    # Send request
    params = data
    scheme = 'https' if use_ssl else 'http'
    url = scheme + '://127.0.0.1:8000/crowds/%s/retainer/finish'
    try:
        response = urllib2.urlopen(url%crowd,
                                   urllib.urlencode(params))
    except urllib2.HTTPError as exc:
        logging.error("HTTPError occurred while reaching crowd_server")
        logging.error("Code: %i, Reason: %s", exc.code, exc.reason)
        logging.error("Server Response Below")
        logging.error(exc.read())
        sys.exit(1)
    res = json.loads(response.read())
    if res['status'] != 'ok' :
        print 'Got something wrong!!!!!!!!!!!!!!!!!!!!!!!!!!!!'
    sys.stdout.flush()
    return res

# Create batches of task
def finish_pool(crowd, pool_id, use_ssl):

    # install custom opener
    if use_ssl:
        urllib2.install_opener(urllib2.build_opener(HTTPSHandlerV3()))

    data = {'pool_id': pool_id}
    return send_request(data, crowd, use_ssl)

def parse_args():
    parser = ArgumentParser(description="Post sample tasks to the crowd server")
    parser.add_argument('--crowd', '-c',
                        choices=['amt', 'internal'], default=['internal'],
                        help=('crowd on which to create tasks. (defaults to '
                              '\'internal\')'))
    parser.add_argument('--pool-id', '-p', required=True,
                        help=('ID of pool to terminate.'))
    parser.add_argument('--ssl', action='store_true',
                        help='Send requests to the crowd server over ssl.')
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()
    logging.info("Finishing pool %s on crowd %s..." % (args.pool_id, args.crowd))
    response = finish_pool(args.crowd, args.pool_id, args.ssl)
    logging.info("Response: %s" % response)
    logging.info("Done!")
