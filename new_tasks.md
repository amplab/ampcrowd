---
layout: site
title: Adding Task Types
---

# Adding Task Types to AMPCrowd
{% include toc.md %}

You can add your own type of crowd task by following the following steps:

1. Define the API for creating such task, including:

   * Defining the type name of this task, which is a string.

   * Defining the group_context for this task, which is a **json object**
     (json array or json dictionary).

   * Defining the content for each point, which is a **json object**. Note that
     the `'content'` field should be a json dictionary mapping ids of points to
     their contents:

         {
             "id1": "content here",
             "id2": "content here"
         }

1. Find  `/basecrowd/templates/basecrowd/TYPE.html`, copy the file to
   `TYPENAME.html` where `TYPENAME` is the type name of this task which was
   defined by you in step 1.

1. Open this file, follow the comments in the file to create the template for
   this task.
