---
layout: site
title: Supported Crowds
---

# Supported Crowds
{% include toc.md %}

The following crowds can be used to process tasks with the crowd server:

## Amazon Mechanical Turk ([https://www.mturk.com]())

* CROWD_NAME: `'amt'`

* Special configuration keys:

  * **Required**:

    * `sandbox` : true/false, indicating whether to use the amt sandbox or the
      full platform.

  * **Optional**:

    * `title`: the title that will show up in AMT's HIT listings
      (default: 'Generic HIT').

    * `descripton`: the description that will show up in AMT's HIT listings
      (default: 'This is a HIT to run on AMT.').

    * `reward`: a float containing the number of dollars to pay for each
      assignment (default: 0.03).

    * `duration`: the expected amount of time a worker should spend on each
      assignment, in minutes (default: 60).

    * `frame_height`: the height of the iframe in which workers will see the
      assignment (default: 800).
* `assignment_context`: urlencoded key-value pairs that must include the
  following keys:

  * `hitId`: the AMT HIT id for the requested task.

  * `worker_id`: the id of the Turker who is assigned to the task, not present
    if the worker is previewing the task rather than working on it.

  * `turkSubmitTo`: the url which must be posted to in order for AMT to register
    that the task has been completed.

  * `assignmentId`: the unique id assigning this worker to this HIT, given the
    special value `ASSIGNMENT_ID_NOT_AVAILABLE` if the worker is previewing the
    task rather than working on it.

* `response_context`: x-www-form-urlencoded key-value pairs that must include
  the following keys:

  * `answers`: the results of the task (dependent on the
    [task type](available_types.html)).

  * `HITId`: the AMT HIT id for the task.

  * `workerId: the id of the Turker who is responding to the task.

  * `assignmentId`: the unique id assigning this worker to this HIT.

## Internal Crowd

The internal crowd is a local interface that can be used for testing new task
types quickly, or for sending tasks to private in-house crowd workers.

* CROWD_NAME: 'internal'

* Special configuration keys: None

* `assignment_context`: urlencoded key-value pairs that must include the
  following keys:

  * `worker_id`: the id of the worker assigned to the task.

  * `task_type`: the type of task to assign to the worker.

* `response_context`: x-www-form-urlencoded key-value pairs that must include
  the following keys:

  * `answers`: the results of the task (dependent on the
    [task type](available_types.html)).

  * `task_id`: the unique id for the task.

  * `worker_id`: the unique id of the worker responding to the task.

  * `assignment_id`: the unique id assigning this worker to this task.
