#!/bin/bash

# Kill old processes
./scripts/stop.sh

# Process options

# Print errors directly to console for easy debugging with -d
if [ "$1" == "-d" ] || [ "$2" == "-d" ]
then
    export DEVELOP=1
    python ampcrowd/manage.py celeryd -l DEBUG &
else
    export DEVELOP=0
    python ampcrowd/manage.py celeryd_detach
fi

# Enable SSL
if [ "$1" == "-s" ] || [ "$2" == "-s" ]
then
    export SSL=1
else
    export SSL=0
fi

# Fire up amqp
rabbitmq-server -detached

# Run the application
python ampcrowd/manage.py collectstatic --noinput
pushd ampcrowd > /dev/null
gunicorn -c ../deploy/gunicorn_config.py crowd_server.wsgi:application
popd > /dev/null
