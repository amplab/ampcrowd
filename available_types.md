---
layout: site
title: Supported Task Types
---

# Supported Task Types

The following tasks are available by default with AMPCrowd, though you can
always [add new task types](new_tasks.html).

Each task type has a **type string** to identify the type, a **group context**
to represent shared state across a group of tasks of that type, **content**
to represent the data associated with a single task of that type, and an
**answer format** for returned results.

All task types support an **optional** `instruction` field in their group
context which may contain HTML-based instructions for the task.

## Sentiment Analysis

This task type represents a crowd task to label a text string with its sentiment
on a scale from 1 (very negative) to 5 (very positive).

* Type string: `'sa'`

* Group Context: none beyond the optional instruction field, e.g.:

      {
          "instruction": "<p>For each tweet, decide whether it is positive,
                              negative, or neutral</p>"
      }

* Content: a json dictionary mapping unique ids to text strings, e.g.:

      {
          "aedcf13": "Arsenal won the 4th again!",
          "4abf23d": "Theo Walcott broke the ligament in his knee last season.",
          "cf8f93b": "Lebron James back to Cavs--guess he can't handle the Heat."
      }

* Answers: values should be an integer in `[1,5]` corresponding to:

  * **1**: Tweet is very negative.
  * **2**: Tweet is somewhat negative.
  * **3**: Tweet is neutral.
  * **4**: Tweet is somewhat positive.
  * **5**: Tweet is very positive.

## Entity Resolution

This task type represents a crowd task to determine whether two records refer to
the same real-world entity or not.

* Type string: `'er'`

* Group Context: In addition to the optional instructions, the `group_context`
  consists of the shared schema for each pair of records. For example:

      {
          "instruction": "<p>Decide whether two records in each group refer to
                          the <b>same entity</b>.</p>",
          "fields": ["price", "location"]
      }

* Content: Content should consist of pairs of records for entity resolution,
  specified as a json dictionary with pairs of records mapped to unique ids, e.g:

      {
          "aedcf13": [["5", "LA"], ["6", "Berkeley"]],
          "4abf23d": [["80", "London"], ["80.0", "Londyn"]]
      }

* Answers: values should be either **0.0** or **1.0**, indicating 'records do
  not match' or 'records match', respectively.

## Filtering

This task type represents a crowd task to answer simple yes or no questions
about individual records.

* Type string: `'ft'`

* Group Context: In addition to the optional instructions, the `group_context`
  consists of the shared schema of the records in each question. It is similar
  to the `group_context` of an entity resolution task. For example:

      {
          "instruction": "<p>Answer the question in each group.</p>",
          "fields": ["Conference", "First Author"]
      }

* Content: The content for each point should be a json dictionary consisting of
  the question title and the values for each data field for the record, e.g:

      {
          "aedcf13": {
              "title": "Is this a paper by Michael Franklin (UC Berkeley)?",
              "record": ["icde", "Michael Franklin"]
          }
      }

* Answers: values should be either **0.0** or **1.0**, indicating 'the answer to
  the question is NO' or 'the answer to the question is YES' respectively.
