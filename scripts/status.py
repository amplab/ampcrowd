# Set up Django environment.
# If you want to use django models in other scripts, import this script first.
import os
import sys
CUR_DIR = "/root/ampcrowd/ampcrowd"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "crowd_server.settings")
sys.path.append(CUR_DIR)
os.chdir(CUR_DIR)

# This is so models get loaded.
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()

# Normal imports here.
import time
from amt.models import RetainerPool
from basecrowd.views import _finish_pool
from basecrowd.interface import CrowdRegistry

def main():
    p = RetainerPool.objects.filter(status__lt=6)
    while True:
        print p
        time.sleep(2)

if __name__ == '__main__':
    main()
