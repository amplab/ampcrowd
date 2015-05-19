---
layout: site
title: Installation Instructions
---

# Installation Instructions
{% include toc.md %}


## Install and Set up Postgres

1. Install postgres and libpq-dev:

       $ sudo apt-get install postgresql
       $ sudo apt-get install libpq-dev

1. Set up access control:

   * Find the `pg_hba.conf` file:

         $ sudo -u postgres psql
         > show hba_file;
         > \q

   * Edit the `pg_hba.conf` file, and change the line starting with `"local"` to
     `"local" "all" "all" "trust"`

1. Restart postgres:

       $ sudo /etc/init.d/postgresql restart

1. Create the Django DB user (right now it's `'ampcrowd'`):

       $ sudo -su postgres
       $ createuser --superuser ampcrowd
       $ exit
       $ psql -u sampleclean
       > \password
       > (Enter 'ampcrowd')
       > \q

1. Create a database (right now it's `'ampcrowd'`):

       $ createdb -O ampcrowd -U ampcrowd ampcrowd

## Install RabbitMQ

    $ sudo apt-get install rabbitmq-server

## Set up your python environment and install requirements

1. Set up a vritual environment for python development (I like
   [virtualenvwrapper](http://virtualenvwrapper.readthedocs.org/en/latest/)).

1. Install the python requirements:

       $ pip install -r requirements.txt

## Set up Amazon Mechanical Turk access
(Skip this step if you don't want to run tasks on AMT.)

1. Create your own private settings file:

       $ cp ampcrowd/crowd_server/private_settings.py.default ampcrowd/crowd_server/private_settings.py

1. Sign up for a mechanical turk account, and put the credentials in
   `private_settings.py` **NEVER CHECK THIS FILE INTO THE REPO.**

## Set up the database and run the server

1. Reset the database:

       $ ./scripts/reset_db.sh

1. Run the server:

       $ ./scripts/run.sh    # Daemon mode
       $ ./scripts.run.sh -d # Debug mode
       $ ./scripts.run.sh -s # Use ssl (can be combined with -d)

1. Make sure it works:

   2. Run the sample script to create a couple of sample HITs on AMT, and a
      couple of hits locally:

          $ ./scripts/post.py -c amt internal -t TASK_TYPES

      `TASK_TYPES` can be 1 or more of 'sa', 'er', 'ft', as described in the
      [User API documentation](/user_api.html).

   2. Log into the AMT management interface
      ([https://requestersandbox.mturk.com/mturk/manageHITs]()) and verify that
      you have just created the sample HITs.

   2. Log in as a worker ([https://workersandbox.mturk.com/mturk/searchbar]())
      and verify that you can accept the HIT and that it displays correctly in
      AMT's iframe. (Note: crowd server must be running with SSL in order to
      view tasks on AMT).

   2. Pull up the [internal crowd interface](https://127.0.0.1/crowds/internal)
      to verify that the tasks have been created and can be completed.

1. To stop the server:

       $ CTRL-C             # if you ran in debug mode
       $ ./scripts/stop.sh  # to clean up stray processes regardless