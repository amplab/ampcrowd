from datetime import datetime, timedelta

from django.db import models
from django.db.models.signals import class_prepared
from django.conf import settings
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone


# Model for a group of tasks
class TaskGroupRetainerStatus:
    WAITING = 0 # Waiting for a pool to recruit workers
    RUNNING = 1 # Running in a retainer pool
    DONE = 2 # Completed


class AbstractCrowdTaskGroup(models.Model):

    # Status of this task group in its retainer pool, if it has one.
    RETAINER_STATUSES = (
        (TaskGroupRetainerStatus.WAITING, 'waiting'),
        (TaskGroupRetainerStatus.RUNNING, 'running'),
        (TaskGroupRetainerStatus.DONE, 'done'),
    )
    retainer_pool_status = models.IntegerField(
        choices=RETAINER_STATUSES, null=True)

    # The retainer pool that will run this group of tasks, a many-to-one
    # relationship. The relationship will be auto-generated to the retainer_pool
    # class of the registered crowd, and can be accessed via the 'retainer_pool'
    # attribute. The related_name will be 'task_groups' to enable reverse
    # lookups. This field will be null if the task group isn't executed by a
    # retainer pool. e.g. retainer_pool = models.ForeignKey(
    #   RetainerPool, null=True, related_name='task_groups')

    # The group id
    group_id = models.CharField(primary_key=True, max_length=64)

    # The number of tasks in this group that have been finished
    tasks_finished = models.IntegerField()

    # The call back URL for sending results once complete
    callback_url = models.URLField()

    # Context for rendering the tasks to the crowd, as a JSON blob.
    group_context = models.TextField()

    # The configuration specific to current crowd type
    crowd_config = models.TextField()

    # All of the configuration passed in when this task was created
    global_config = models.TextField()

    # When the group was created
    created_at = models.DateTimeField(default=timezone.now)

    # When work on the tasks began
    work_start_time = models.DateTimeField(null=True)

    def __unicode__(self):
        ret = "Task Group %s" % self.group_id
        if self.retainer_pool_status is not None:
            ret += " (retainer)"
        return ret

    class Meta:
        abstract = True


# Model for an individual task.
class AbstractCrowdTask(models.Model):

    # The group that this task belongs to, a many-to-one relationship.
    # The relationship will be auto-generated to the task_group class of the
    # registered crowd, and can be accessed via the 'group' attribute.
    # The related_name will be 'tasks' to enable reverse lookups, e.g.
    # group = models.ForeignKey(CrowdTaskGroup, related_name='tasks')

    # The type of the task, Sentiment Analysis, Deduplication, etc
    task_type = models.CharField(max_length=64)

    # The data for the task, specific to the task type (stored as a JSON blob)
    data = models.TextField()

    # Creation time
    create_time = models.DateTimeField()

    # Unique identifier for the task
    task_id = models.CharField(primary_key=True, max_length=64)

    # The number of assignments (i.e., number of votes) to get for this task.
    num_assignments = models.IntegerField()

    # Answer based on majority vote
    mv_answer = models.TextField()

    # Answer based on Expectation Maximization
    em_answer = models.TextField()

    # Has the task received enough responses?
    is_complete = models.BooleanField(default=False)

    # Is this task a retainer task?
    is_retainer = models.BooleanField(default=False)

    # Has the task been retired?
    is_retired = models.BooleanField(default=False)

    def __unicode__(self):
        task_str = "Task %s (type %s): %s" % (self.task_id, self.task_type,
                                              self.data)
        return task_str + " [retainer]" if self.is_retainer else task_str

    class Meta:
        abstract = True


# Model for workers
class AbstractCrowdWorker(models.Model):

    # The tasks a worker has been assigned, a many-to-many relationship.
    # The relationship will be auto-generated to the task class of the
    # registered crowd, and can be accessed via the 'tasks' attribute.
    # The related_name will be 'workers' to enable reverse lookups, e.g.
    # tasks = models.ManyToManyField(CrowdTask, related_name='workers')

    # A unique id for the worker
    worker_id = models.CharField(primary_key=True, max_length=64)

    # The last time this worker pinged the server from a retainer pool
    last_ping = models.DateTimeField(null=True)

    # Has this worker read the retainer pool instructions?
    understands_retainer = models.BooleanField(default=False)

    # Find the tasks the worker has completed during this session in
    # the pool. Avoid double-counting tasks between sessions.
    def completed_assignments_for_pool_session(self, session_task):
        assert session_task.task_type == 'retainer'
        return session_task.work_assignments.filter(
            worker=self, 
            finished_at__isnull=False)

    def __unicode__(self):
        return "Worker %s" % self.worker_id

    class Meta:
        abstract = True


# Model for a worker's assignment to a task
class AbstractCrowdWorkerAssignment(models.Model):

    # The task that was assigned, a many-to-one relationship.
    # The relationship will be auto-generated to the task class of the
    # registered crowd, and can be accessed via the 'task' attribute.
    # The related_name will be 'assignments' to enable reverse lookups, e.g.
    # task = models.ForeignKey(CrowdTask, related_name='assignments')

    # The retainer session task that this assignment pertains to, a
    # many-to-one relationship. The relationship will be auto-generated
    # to the task class of the registered crowd, and can be accessed via 
    # the 'retainer_session_task' attribute. The related_name will be 
    # 'work_assignments' to enable reverse lookups, e.g.
    # retainer_session_task = models.ForeignKey(CrowdTask, related_name='work_assignments')

    # The worker who was assigned, a many-to-one relationship.
    # The relationship will be auto-generated to the worker class of the
    # registered crowd, and can be accessed via the 'worker' attribute.
    # The related_name will be 'assignments' to enable reverse lookups, e.g.
    # worker = models.ForeignKey(CrowdWorker, related_name='assignments')

    # The content of the response (specific to the task type).
    content = models.TextField(null=True)

    # The assignment id of this response
    assignment_id = models.CharField(max_length=200, primary_key=True)

    # The time the assignment was created
    assigned_at = models.DateTimeField(default=timezone.now)

    # Rejection time, if the task was rejected
    rejected_at = models.DateTimeField(null=True)

    # The time the assignment was completed
    finished_at = models.DateTimeField(null=True)

    # Was this assignment terminated before the worker submitted work?
    terminated = models.BooleanField(default=False)

    # Fields related to paying the worker
    #####################################

    # The time the assignment received payment
    paid_at = models.DateTimeField(null=True)

    # The amount paid as a bonus
    amount_paid_bonus = models.FloatField(default=0.0)
    
    # The amount paid as task price
    amount_paid_list = models.FloatField(default=0.0)

    # Convenience method: total amount paid
    @property
    def amount_paid(self):
        return round(self.amount_paid_bonus + self.amount_paid_list, 2)

    # The amount to pay for this assignment
    def compute_bonus(self, waiting_rate, task_rate, list_rate, logger=None):
        wait_minutes = self.time_waited / 60.0
        waiting_payment = waiting_rate * wait_minutes
        tasks_completed = self.worker.completed_assignments_for_pool_session(
            self.task).count()
        task_payment = task_rate * tasks_completed - list_rate
        total_bonus = waiting_payment + task_payment

        message = ("You completed %d tasks and waited %.2f minutes on a retainer "
                   "pool task. Thank you for your work!" % (tasks_completed,
                                                            round(wait_minutes, 2)))
        if logger:
            logger.info("Paying %f x %f + %f x %d - %f = %f dollars to %s for %s" % (
                    waiting_rate, wait_minutes, task_rate, tasks_completed,
                    list_rate, total_bonus, self.worker, self))
        return (round(total_bonus, 2), message)


    # Fields related to tracking the worker's wait time
    ###################################################

    # The last time someone working on this task pinged the server from a
    # retainer pool
    last_ping = models.DateTimeField(null=True)

    # Cumulative waiting time, in seconds
    time_waited_total = models.FloatField(default=0)

    # Waiting time this session, in seconds
    time_waited_session = models.FloatField(default=0)

    # Convenience method for accessing total time waited.
    @property
    def time_waited(self):
        return round(self.time_waited_total + self.time_waited_session, 2)

    # Finish a waiting session
    def finish_waiting_session(self):
        self.time_waited_total += self.time_waited_session
        self.time_waited_session = 0
        # Don't save: caller must save the object.

    # The time the assignment took, in seconds
    @property
    def length(self):
        if self.finished_at:
            return (self.finished_at - self.assigned_at).total_seconds()
        return None

    def __unicode__(self):
        return "Assignment %s: %s to %s" % (self.assignment_id, self.worker, self.task)

    class Meta:
        abstract = True

# Status of a Retainer Pool
class RetainerPoolStatus:
    CREATED = 1 # Pool has been created, but there are no workers yet
    RECRUITING = 2 # Pool is recruiting workers to capacity
    IDLE = 3 # Pool is at worker capacity, and is ready to run tasks
    ACTIVE = 4 # Pool is running task groups
    REFILLING = 5 # Pool is running task groups, but workers have left and more are needed
    FINISHED = 6 # Pool has been terminated


# Model for a pool of workers
class AbstractRetainerPool(models.Model):

    # The workers in the pool, a many-to-many relationship.
    # The relationship will be auto-generated to the worker class of the
    # registered crowd, and can be accessed via the 'workers' attribute.
    # The related_name will be 'pools' to enable reverse lookups, e.g.
    # workers = models.ManyToManyField(CrowdWorker, related_name='pools')

    # The status of this pool.
    STATUSES = (
        (RetainerPoolStatus.CREATED, 'created'),
        (RetainerPoolStatus.RECRUITING, 'recruiting'),
        (RetainerPoolStatus.IDLE, 'idle'),
        (RetainerPoolStatus.ACTIVE, 'active'),
        (RetainerPoolStatus.REFILLING, 'refilling'),
        (RetainerPoolStatus.FINISHED, 'finished'),
    )
    status = models.IntegerField(choices=STATUSES,
                                 default=RetainerPoolStatus.CREATED)

    # The last time at which recruitment tasks were posted
    last_recruited_at = models.DateTimeField(
        default=timezone.now() -
        timedelta(seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS))

    # Number of workers desired in the pool
    capacity = models.IntegerField()

    # External identifier for this pool (for re-use)
    external_id = models.CharField(max_length=200, unique=True)

    def save(self, *args, **kwargs):
        models.Model.save(self, *args, **kwargs)
        if self.external_id == '':
            self.external_id = str(self.id)
            self.save()

    def __unicode__(self):
        return "<Retainer Pool %s: %d active workers, capacity %d, status %s>" % (
            self.external_id, self.active_workers.count(), self.capacity,
            self.get_status_display())

    @property
    def active_workers(self):
        time_cutoff = timezone.now() - timedelta(
            seconds=settings.PING_TIMEOUT_SECONDS)
        return self.workers.filter(assignments__task__task_type='retainer',
                                   assignments__task__group__retainer_pool=self,
                                   assignments__last_ping__gte=time_cutoff)

    def expired_tasks(self, task_model):
        time_cutoff = timezone.now() - timedelta(
            seconds=settings.RETAINER_WORKER_TIMEOUT_SECONDS)
        return (task_model.objects.filter(
                task_type='retainer',
                group__retainer_pool=self)
                .exclude(
                assignments__last_ping__gte=time_cutoff))

    def new_expired_tasks(self, task_model):
        # expired workers with a retainer task that hasn't been marked retired.
        return self.expired_tasks(task_model).filter(is_retired=False)

    class Meta:
        abstract = True


# A "proto-task" that loads for a retainer pool.
# Not abstract because crowd implementations should never see it.
# Just used to share state between runs of the post_retainer_tasks celery task.
class RetainerTask(models.Model):

    # Is this task active on the crowd site?
    active = models.BooleanField(default=True)

    # When was this task posted?
    created_at = models.DateTimeField(auto_now_add=True)

    # The actual task created (a generic foreign key, since it must point to the
    # task class of multiple crowds).
    content_type = models.ForeignKey(ContentType)
    object_id = models.CharField(max_length=64)
    task = generic.GenericForeignKey('content_type', 'object_id')

    # The crowd this task runs on
    crowd_name = models.CharField(max_length=64)

    def __unicode__(self):
        return "Retainer Task %d" % self.id


# Register a set of models as a new crowd.
class CrowdModelSpecification(object):
    def __init__(self, crowd_name,
                 task_model,
                 group_model,
                 worker_model,
                 assignment_model,
                 retainer_pool_model=None):
        self.name = crowd_name
        self.task_model = task_model
        self.group_model = group_model
        self.worker_model = worker_model
        self.assignment_model = assignment_model
        self.retainer_pool_model = retainer_pool_model

    @staticmethod
    def add_rel(from_cls, to_cls, relation_cls, relation_name,
                related_name=None, **field_kwargs):
        field = relation_cls(to_cls, related_name=related_name, **field_kwargs)
        field.contribute_to_class(from_cls, relation_name)

    def add_model_rels(self):
        # tasks belong to groups
        self.add_rel(self.task_model, self.group_model, models.ForeignKey,
                     'group', 'tasks')

        # assignments are assigned to a worker
        self.add_rel(self.assignment_model, self.worker_model, models.ForeignKey,
                     'worker', 'assignments')

        # assignments pertain to a task
        self.add_rel(self.assignment_model, self.task_model, models.ForeignKey,
                     'task', 'assignments')

        if self.retainer_pool_model:
            # pools contain workers
            self.add_rel(self.retainer_pool_model, self.worker_model,
                         models.ManyToManyField, 'workers', 'pools')

            # task groups might be run by pools
            self.add_rel(self.group_model, self.retainer_pool_model,
                         models.ForeignKey, 'retainer_pool', 'task_groups',
                         null=True)
            
            # assignments fall under a particular retainer pool session.
            self.add_rel(self.assignment_model, self.task_model, models.ForeignKey,
                         'retainer_session_task', 'work_assignments', null=True)
