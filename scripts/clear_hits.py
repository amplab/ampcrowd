#!/usr/bin/env python
import logging
from argparse import ArgumentParser
from boto.mturk.connection import MTurkConnection, MTurkRequestError

import os
import sys
os.environ['DJANGO_SETTINGS_MODULE'] = 'crowd_server.settings'
sys.path.append('ampcrowd')
from django.conf import settings

logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())

def main(options):
    purge_all_hits(options)

def get_amt_connection(sandbox):
    """ Get a connection object to communicate with the AMT API. """
    host = (settings.AMT_SANDBOX_HOST
            if sandbox else settings.AMT_HOST)
    return MTurkConnection(aws_access_key_id=settings.AMT_ACCESS_KEY,
                           aws_secret_access_key=settings.AMT_SECRET_KEY,
                           host=host)

def purge_all_hits(options):
    conn = get_amt_connection(options.sandbox)
    logger.info("Fetching HITs...")
    hits = list(conn.get_all_hits())
    logger.info("%d HITs found." % len(hits))
    logger.info("Disabling all HITs...")
    for hit in hits:
        try:
            conn.disable_hit(hit.HITId)
            logger.debug("Disabled HIT %s." % hit.HITId)
        except MTurkRequestError as e:
            logger.exception("Error disabling hit %s." % hit.HITId)
    logger.info("Done disabling HITs.")


def parse_args():
    parser = ArgumentParser(description="Remove all HITs from an AMT account.")
    parser.add_argument('--sandbox', '-s', action='store_true', default=False,
                        help='delete HITs from an AMT sandbox account.')
    parser.add_argument('--verbose', '-v', action='store_true', default=False,
                        help='verbose logging.')
    args = parser.parse_args()
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger.setLevel(log_level)
    return args

if __name__ == "__main__":
    main(parse_args())
