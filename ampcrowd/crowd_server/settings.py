"""
Django settings for crowd_server project.

For more information on this file, see
https://docs.djangoproject.com/en/1.6/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/1.6/ref/settings/
"""

# Build paths inside the project like this: os.path.join(BASE_DIR, ...)
import os
import json
from urllib2 import urlopen
import djcelery

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DEV_MODE = os.environ.get('DEVELOP', False) == "1"
SSL_MODE = os.environ.get('SSL', False) == "1"

LOGGING = {
    'version': 1,
    'disable_existing_loggers': True,
    'formatters': {
        'simple': {
            'format': '[%(name)s:%(levelname)s] %(message)s'
        }
    },
    'handlers': {
        'file': {
            'level': 'DEBUG',
            'class': 'logging.FileHandler',
            'filename': 'django.log',
        },
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'simple'
        }
    },
    'loggers': {
        'django': {
            'level': 'DEBUG',
            'propagate': True,
        },
        'crowd_server': {
            'level': 'DEBUG',
        }
    },
}
# Settings for production
if not DEV_MODE:
    # Don't validate hostnames, since we'll be moving IPs around.
    ALLOWED_HOSTS = '*'

    # Dump all logs to the file 'django.log'
    LOGGING['loggers']['django']['handlers'] = ['file']
    LOGGING['loggers']['crowd_server']['handlers'] = ['file']
else:
    ALLOWED_HOSTS = []
    LOGGING['loggers']['crowd_server']['handlers'] = ['console']

if SSL_MODE:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True

# Celery Configuration
djcelery.setup_loader()

# Set broker using hosts entry for 'rabbitmq'. This is set for Docker but can be set to alias
# localhost in /hosts/etc if needed
BROKER_URL = "amqp://guest:guest@rabbitmq:5672//"

# Settings for the AMT app
# AMT_SANDBOX = True # run on the sandbox, or on the real deal?
AMT_SANDBOX_HOST = 'mechanicalturk.sandbox.amazonaws.com'
# AMT_SANDBOX_WORKER_SUBMIT = 'https://workersandbox.mturk.com/mturk/externalSubmit'
AMT_HOST = 'mechanicalturk.amazonaws.com'
POST_BACK_AMT = 'https://www.mturk.com/mturk/externalSubmit'
POST_BACK_AMT_SANDBOX = 'https://workersandbox.mturk.com/mturk/externalSubmit'

# If True, fetch public facing IP address and use as callback, else set to crowd_host
HAVE_PUBLIC_IP = False

PUBLIC_IP = json.loads(urlopen('http://jsonip.com').read())['ip'] if HAVE_PUBLIC_IP else None

# Set the callback for the crowd tasks. For development use /etc/hosts to set crowd_server correctly.
AMT_CALLBACK_HOST = os.environ.get('AMT_CALLBACK_HOST', 'crowd_server:8000')

AMT_DEFAULT_HIT_OPTIONS = { # See documentation in amt/connection.py:create_hit
    'title': 'Generic HIT',
    'description': 'This is a HIT to run on AMT.',
    'reward': 0.03,
    'duration': 60,
    'num_responses': 3,
    'frame_height': 800,
    'use_https': True,
}

AMT_ACCESS_KEY = os.environ.get('AWS_ACCESS_KEY_ID', '')
AMT_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', '')

# AMT Settings that MUST be defined in private_settings.py:
#   AMT_ACCESS_KEY
#   AMT_SECRET_KEY

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/1.6/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '009&#y4^ix8uzt5wt^5d%%+2xp@ym&hfv%%y*xk4obcro-1@r6'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = DEV_MODE

TEMPLATE_DEBUG = DEBUG

APPEND_SLASH = True

# Application definition

INSTALLED_APPS = (
    #'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'sslserver',
    'djcelery',
    'basecrowd',
    'amt',
    'internal',
    'results_dashboard',
)

MIDDLEWARE_CLASSES = (
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'amt.connection.AMTExceptionMiddleware'
)

ROOT_URLCONF = 'crowd_server.urls'

WSGI_APPLICATION = 'crowd_server.wsgi.application'


# Database
# https://docs.djangoproject.com/en/1.6/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME': 'ampcrowd',
        'USER': 'ampcrowd',
        'PASSWORD': 'ampcrowd',
        'HOST': 'db',
        'PORT': '5432',
    }
}

# Internationalization
# https://docs.djangoproject.com/en/1.6/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_L10N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/1.6/howto/static-files/
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')


# Import private settings, overriding settings in this file
try:
    from private_settings import *
except ImportError:
    pass
