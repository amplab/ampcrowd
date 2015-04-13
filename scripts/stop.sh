#!/bin/bash

# Kill celery workers
ps auxww | grep 'celery worker' | grep -v 'grep' | awk '{print $2}' | xargs kill -9
ps auxww | grep 'celeryd' | grep -v 'grep' | awk '{print $2}' | xargs kill -9

# Kill remaining crowd_server python processes
ps auxww | grep 'ampcrowd' | grep 'python' | grep -v 'grep' | awk '{print $2}' | xargs kill -9

# Kill rabbitmq
ps auxww | grep 'rabbitmq' | grep -v 'grep' | awk '{print $2}' | xargs kill -9
