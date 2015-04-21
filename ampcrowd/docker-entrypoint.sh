#!/usr/bin/env bash

# Migrate databases if necessary
sleep 5
python ampcrowd/manage.py syncdb --noinput

# Process options

# Enable SSL
if [ "$1" == "-s" ] || [ "$2" == "-s" ]
then
    export SSL=1
else
    export SSL=0
fi

python ampcrowd/manage.py collectstatic --noinput

# Print errors directly to console for easy debugging with -d
if [ "$1" == "-d" ] || [ "$2" == "-d" ]
then
    export DEVELOP=1
    python ampcrowd/manage.py celeryd -l DEBUG &
    python ampcrowd/manage.py runserver 0.0.0.0:8000
else
    export DEVELOP=0
    python ampcrowd/manage.py celeryd_detach
    (cd ampcrowd && gunicorn -c ../deploy/gunicorn_config.py crowd_server.wsgi:application)
fi
