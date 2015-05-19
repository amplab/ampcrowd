---
layout: site
title: User-Facing APIs
---

# Web Service User-Facing APIs

## Create tasks for a group of points.

The main interface for using AMPCrowd is the API call to register a group of
tasks with the server and begin processing them.

* URL: `POST /crowds/CROWD_NAME/tasks/`, where `CROWD_NAME` is one of the
  [available crowds](available_crowds.html).

* Data: There is only one single field, `data`, which maps to a json dictionary
  with keys:

  * **configuration**: settings for this group of points, a json dictionary
    with keys:

    * **task_type**: The type of this task, e.g, `'sa'` for sentiment analysis.
      Must be one of [the available task types](available_types.html)

    * **task_batch_size**: The maximum number of points to show a crowd worker
      in a single task (integer).

    * **num_assignments**: The number of crowd votes to acquire for each task
      (integer).

    * **callback_url**: The URL to POST results to (see below for the arguments
      to that call).

    * **CROWD_NAME**: A json dictionary with configuration specific to the crowd
      running the tasks. See
      [the list of available crowds](available_crowds.html) for the
      configuration keys supported by specific crowds.

  * **group_id**: A unique identifier for this group of points.

  * **group_context**: A json dictionary that represents the context that is
    shared among all the points in the group. The contents of the dictionary
    depend on the task type (see
    [the available task types](available_types.html) for examples).

  * **content**: Data necessary to render the crowd interface for the selected
    task type (see [the available task types](available_types.html) for
    examples).

* Examples:

      data={
          "configuration": {
              "task_type": "sa",
              "task_batch_size": 2,
              "num_assignments": 1,
              "callback_url": "http://mysite.com/crowd/responses"
          },
          "group_id": "GroupId1",
          "group_context": {},
          "content": {
              "tweetId1": "this is a tweet!",
              "tweetId2": "this is another tweet"
          }
      }

      data={
          "configuration": {
              "task_type": "er",
              "task_batch_size": 1,
              "num_assignments": 1,
              "callback_url": "http://mysite.com/crowd/responses/"
          },
          "group_id": "GroupId2",
          "group_context": {
              "fields": ["age","name"]
          },
          "content": {
              "recordPairId1": [["22","James"],["21","Wenbo"]]
          }
      }

      data={
          "configuration": {
              "task_type": "ft",
              "task_batch_size": 1,
              "num_assignments": 1,
              "callback_url": "http://mysite.com/crowd/responses/"
          },
          "group_id": "GroupId3",
          "group_context": {
              "fields": ["Conference","First Author"]
          },
          "content": {
              "filterRecordId1": {
                  "title": "Decide whether the following paper is by Michael Franklin.",
                  "record": ["icde", "Michael Franklin"]
              },
              "filterRecordID2": {
                  "title": "Decide whether the following paper is by Jiannan Wang.",
                  "record" : ["nsdi", "Zhao Zhang"]
              }
          }
      }

* Response: This request should return an HTTP 200 OK response, containing a
  simple json dictionary, one of the following:

      {"status": "ok"}

  or

      {"status": "wrong"}

  The latter means that the format is incorrect, which may be due to incorrectly
  formatted json content or omission of one or more required fields.

## Receive results at a callback URL

When a point has been processed by sufficient crowd workers (according to the
`configuration.num_assignments` parameter passed into the create task group call
above), the quality-controlled answer will be sent back to the user.

* URL: `POST CB_URL`, where `CB_URL` is the `configuration.callback_url`
  parameter passed into the create task group call.

* Data: The results that are sent back consist of a single urlencoded field,
  `'data'`, which maps to a json dictionary with keys:

  * **group_id**: a string specifying the group that this point belongs to.

  * **answers**: a list of 1 or more responses for points in the group, each of
    which contains:

    * **identifier**: the identifier of the point given when the group was
      created.

    * **value**: the answer value. Values depend on the type of the crowd task
      (see the [list of available types](available_types.html)).

* Examples:

      data={
          "group_id": "GroupId1",
          "answers": [
              {
                  "identifier": "tweetId1",
                  "value": 1
              },
              {
                  "identifier": "tweetId2",
                  "value": 3
              }
          ]
      }

      data={
          "group_id": "GroupId2",
          "answers": [
              {
                  "identifier": "recordPairId1",
                  "value": 0.0
              }
          ]
      }

## Delete in-progress tasks.

To delete all currently existing tasks in the system registered to a single
crowd platform, there is a simple API call. This call also deletes tasks from
the remote crowd platform, for example, on MTurk, tasks will be disabled.

* URL: `GET /crowds/CROWD_NAME/purge_tasks/`, where `CROWD_NAME` is one of the
  [available crowds](available_crowds.html).

* Response: HTTP 200 OK, with no response body.
