#!/bin/bash

# Kill old processes
./scripts/stop.sh

# Fire up amqp
rabbitmq-server -detached

# Process options

# Print errors directly to console for easy debugging with -d
if [ "$1" == "-d" ] || [ "$2" == "-d" ]
then
    export DEVELOP=1
    python ampcrowd/manage.py celery worker -E -l DEBUG -n recruitWorker.%h -c 16 -Q recruit --beat &
    python ampcrowd/manage.py celery worker -E -l DEBUG -n responseWorker.%h -c 16 -Q responses &
    python ampcrowd/manage.py celery flower --port=8020 &
else
    export DEVELOP=0
    python ampcrowd/manage.py celery worker -n recruitWorker.%h -E -c 16 -Q recruit --beat --detach
    python ampcrowd/manage.py celery worker -n responseWorker.%h -E -c 16 -Q responses --detach
    python ampcrowd/manage.py celery flower --port=8020 --detach
fi

# Enable SSL
if [ "$1" == "-s" ] || [ "$2" == "-s" ]
then
    export SSL=1
else
    export SSL=0
fi

# Run the application
python ampcrowd/manage.py collectstatic --noinput
pushd ampcrowd > /dev/null
gunicorn -c ../deploy/gunicorn_config.py crowd_server.wsgi:application
popd > /dev/null
