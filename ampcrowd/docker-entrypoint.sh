#!/usr/bin/env bash

# Migrate databases if necessary, sleep allows postgres container to finish launching
sleep 3
python ampcrowd/manage.py syncdb --noinput

# Generate static content
python ampcrowd/manage.py collectstatic --noinput

# Process options
DEVELOP=0
SSL=0
FOREGROUND=0
while getopts "dsf" OPTION
do
	case $OPTION in
		d)
			DEVELOP=1
			;;
		s)
			SSL=1
			;;
		f)
			FOREGROUND=1
			;;
	esac
done

export DEVELOP
export SSL
export FOREGROUND

if [ "$DEVELOP" -eq  "1" ]
then
	echo "Celery launched in debug mode"
	python ampcrowd/manage.py celeryd -l DEBUG &
	if [ "$SSL" -eq "1" ]
	then
		echo "Gunicorn starting"
		(cd ampcrowd && gunicorn -c ../deploy/gunicorn_config.py crowd_server.wsgi:application)
	else
		python ampcrowd/manage.py runserver 0.0.0.0:8000
	fi
else
	echo "Celery launched in production mode"
	python ampcrowd/manage.py celeryd_detach
	echo "Gunicorn starting"
	(cd ampcrowd && gunicorn -c ../deploy/gunicorn_config.py crowd_server.wsgi:application)
fi
