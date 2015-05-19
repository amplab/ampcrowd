---
layout: site
title: Interfacing with a New Crowd
---

# Interfacing with a new crowd
{% include toc.md %}

The crowd server is designed to be easily extensible to send tasks to other
crowd systems. Each crowd is implemented as a Django app that can re-use models,
views, and templates from our generic 'basecrowd' implementation. To add support
for a new crowd to the server, you must:

1. Assign your crowd a CROWD_NAME that can be referenced from within URLs.

1. Create your django app with `python manage.py startapp CROWD_NAME`.

1. In `CROWD_NAME/models.py`, define your models. A crowd must have at a minimum 
   four models: one for Tasks, one for Task Groups, one for Workers, and one for
   worker Responses. Luckily, we provide abstract implementations of all of
   these in `basecrowd/models.py`, so simply subclass
   `models.AbstractCrowdTask`, `models.AbstractCrowdTaskGroup`,
   `models.AbstractCrowdWorker`, and `models.AbstractCrowdWorkerResponse` and
   you're good to go. You can add custom fields to your subclasses if you'd
   like, but it's probably not necessary.

1. Create a new file `CROWD_NAME/interface.py`. This will be the bulk of your
   work. In the file, create a subclass of `basecrowd.interface.CrowdInterface`,
   and implement any methods necessary to support your crowd. Feel free to look
   at `basecrowd/interface.py` for the full list of available methods, but
   you're likely to need only a few, including:

   * `create_task`: create a new task on your crowd platform.

   * `delete_tasks`: delete one or more tasks on your crowd platform.

   * `get_assignment_context`: when your crowd platform requests a task
     interface, provide enough context to render your interface template
     (described more below).

   * `get_response_context`: when a crowd worker submits data, extract it
     from the request and put it in a format that can be saved to your models.

   Finally, create an instance of your interface as a module-level variable,
   e.g.:

       MYCROWD_INTERFACE = MyCrowdInterface(CROWD_NAME)

1. In `CROWD_NAME/__init__.py`, register your new interface with the server:

       from interface import MYCROWD_INTERFACE
       from models import *
       from basecrowd.interface import CrowdRegistry

       CrowdRegistry.register_crowd(
           MYCROWD_INTERFACE,
           task_model=MyCrowdTaskSubclass,
           group_model=MyTaskGroupSubclass,
           worker_model=MyWorkerSubclass,
           response_model=MyResponseSubclass)

1. Now all we need are templates to render the task interfaces to the crowd.
   Again, we provide a base template that should do almost all of the heavy
   lifting. Create your app's template directories and a new template in
   `CROWD_NAME/templates/CROWD_NAME/base.html`. Inherit from our base template
   with `{{ "{%" }} extends "basecrowd/base.html" %}`, and implement as many blocks in
   that template as you'd like. Our base template (located in
   `basecrowd/templates/basecrowd/base.html`) contains many inheritable blocks,
   but you probably need to implement only one of them:
   `{{ "{%" }} block get_submit_context_func %}`. This block should contain a javascript
   function definition, `get_submit_context(urlParamStrings)` that takes a list
   of URL parameters (unsplit in the form `'key=value'`), and should return a
   JSON object containing any context that needs to be submitted to the server
   with the data (e.g. the task, worker, and assignment ids relevant to this
   interface). See our implementation in `amt/templates/amt/base.html` for
   details.

1. The interfaces for individual task types (sentiment analysis, deduplication,
   etc.) should just fit into your `base.html` template with no additional work.
   If you completely customized `base.html`, however, you might need custom
   templates for the task types as well. To create them, simply follow the steps
   for [creating a new task type](new_tasks.html), but place your template in
   your crowd's template directory (e.g.,
   `CROWD_NAME/templates/CROWD_NAME/sa.html`).

1. And that's it! Now you should be able to use the APIs described above to
   create and complete tasks on your new crowd platform. If you run into
   trouble, take a look at our implementation of the Amazon Mechanical Turk
   crowd (in `amt/`) for inspiration.
