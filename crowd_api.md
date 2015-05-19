---
layout: site
title: Crowd-facing APIs
---

# Crowd Platform-facing APIs

Crowd Platforms may use the following API calls to get tasks for display to
workers and send crowd responses as they complete work.

## Get assignments

This call allows platforms to get a new assignment for display to a worker.

* URL: `GET /crowds/CROWD_NAME/assignments/`, where `CROWD_NAME` identifies one
  of the [available crowds](available_crowds.html).

* Data: custom for each crowd (see each crowd's `assignment_context` in the
  [available crowd platform listing](available_crowds.html)).

* Response: HTTP 200, with HTML for the interface that a crowd worker can use to
  complete the task.

## Post responses

This call allows platforms to send a crowd worker's response to AMPCrowd.

* URL: `POST /crowds/CROWD_NAME/responses/`, where `CROWD_NAME` identifies one
  of the [available crowds](available_crowds.html).

* Data: custom for each crowd (see each crowd's `response_context` in the
  [available crowd platform listing](available_crowds.html)). Data should be
  x-www-form-urlencoded and must contain at least the key:

  * `answers`: the results of the task. Should be a JSON string containing
    key-value pairs, where each key is the unique identifier for a single
    point, and each value is that point's label assigned by the crowd worker.
    See the [available task types](available_types.html) for a structure of
    answers for each task type.

* Response: HTTP 200, with no response data.
