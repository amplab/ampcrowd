from datetime import datetime, timedelta

from django.db import models
from django.db.models.signals import class_prepared
from django.conf import settings
from django.contrib.contenttypes import generic
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone

import numpy as np

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

    # The time when all tasks in the group completed
    finished_at = models.DateTimeField(null=True)

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
    create_time = models.DateTimeField(default=timezone.now)

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

    # Time before celery starts
    pre_celery = models.DateTimeField(null=True)

    # Time before quality control starts
    pre_em = models.DateTimeField(null=True)

    # TIme after quality control finished
    post_em = models.DateTimeField(null=True)

    # Time after celery task is done
    post_celery = models.DateTimeField(null=True)

    # Did this task finish last in the group?
    is_last = models.BooleanField(default=False)

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

    # Worker's mean speed
    mean_speed = models.FloatField(null=True)

    # Worker's speed confidence 
    mean_speed_ci = models.FloatField(null=True)

    # Find tasks the worker was assigned during this pool session.
    def assignments_for_pool_session(self, session_task):
        assert session_task.task_type == 'retainer'
        return session_task.work_assignments.filter(worker=self)

    # Find the tasks the worker completed during this pool session.
    def completed_assignments_for_pool_session(self, session_task):
        return (self.assignments_for_pool_session(session_task)
                .filter(finished_at__isnull=False))

    def compute_speed(self, desired_mean, model_spec, logger=None, alpha=1, **assignment_filter_kwargs):
        if logger:
            logger.info("Computing speed for %s" % self)

        emp_speed, emp_std = self.mean_empirical_speed(desired_mean, **assignment_filter_kwargs) or (0.0, 0.0)
        term_speed, num_tasks, num_terminated_tasks = self.mean_termination_speed(model_spec, alpha=alpha)
        if num_tasks == 0:
            if logger:
                logger.info("Can't compute speed for %s: no available tasks" % self)
            return
        weight = float(num_terminated_tasks) / num_tasks
        #weight = 0
        final_speed = term_speed * weight + emp_speed * (1.0 - weight)
        if logger:
            logger.info('Computed speed for %s. Empirical speed: %f, termination speed: %f, '
                        'termination ratio: %f (%f / %f), overall speed: %f' %
                        (self, emp_speed, term_speed, weight, num_terminated_tasks, num_tasks, final_speed))
        self.mean_speed = final_speed
        self.mean_speed_ci = emp_std * weight
        self.save()

    def mean_termination_speed(self, model_spec, alpha=1):
        # Count our total and terminated tasks
        time_cutoff = timezone.now() - timedelta(minutes=10)
        worker_assignments = (self.assignments
                              .exclude(task__task_type='retainer')
                              .filter(assigned_at__gt=time_cutoff))
        num_tasks = worker_assignments.count()
        if num_tasks == 0:
            return 0.0, 0, 0

        # Ignore last tasks when counting terminated tasks, because almost all last tasks are terminated.
        terminated_assignments = worker_assignments.filter(terminated=True).exclude(task__is_last=True)
        num_terminated_tasks = terminated_assignments.count()

        # find the assignments that terminated this worker
        worker_task_objs = model_spec.task_model.objects.filter(
            assignments__assignment_id__in=terminated_assignments)

        terminating_assignments = (model_spec.assignment_model.objects
                                   .filter(task__in=worker_task_objs)
                                   .filter(terminated=False)
                                   .exclude(worker=self)
                                   .filter(finished_at__isnull=False))

        # Compute their average
        if not terminating_assignments.exists():
            return 0.0, num_tasks, num_terminated_tasks
        average_fast_speed = np.mean([a.length for a in terminating_assignments])
        
        # Our estimated speed given terminated tasks
        return (float(average_fast_speed * (num_tasks + alpha)) / (num_tasks + alpha - num_terminated_tasks),
                num_tasks,
                num_terminated_tasks)

    # Compute the worker's average speed over all the work they've done
    def mean_empirical_speed(self, desired_mean, **assignment_filter_kwargs):
        now = timezone.now()
        time_cutoff = now - timedelta(minutes=10)
        running_cutoff = now - timedelta(seconds=desired_mean)
        all_tasks = (self.assignments
                     .exclude(
                task__task_type='retainer')
                     .filter(
                models.Q(finished_at__isnull=False) |
                models.Q(assigned_at__lte=running_cutoff)) # penalize long-running open assignments
                     .filter(
                models.Q(terminated=False) |
                models.Q(assigned_at__lte=running_cutoff)) # penalize long-running open assignments
                     .filter(**assignment_filter_kwargs))
        all_tasks_recent = all_tasks.filter(
            assigned_at__gte=time_cutoff) # windowed average
        if all_tasks_recent.count() >= 4:
            tasks_qset = all_tasks_recent
        else:
            tasks_qset = all_tasks.order_by('-assigned_at')[:10] # just take the most recent ones.
        task_speeds = [t.length if t.finished_at else (now - t.assigned_at).total_seconds()
                       for t in tasks_qset.iterator()]
        num_tasks = len(task_speeds)
        if num_tasks == 0:
            return None

        # Give the mean, a 95% CI, and the number of assignments completed
        return (np.mean(task_speeds), 1.96 * np.std(task_speeds) / np.sqrt(num_tasks))

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

    # Was the worker's work cut off by the retainer pool closing?
    pool_ended_mid_assignment = models.BooleanField(default=False)

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

        worker_message = ("You completed %d tasks and waited %.2f minutes on a retainer "
                          "pool task. Thank you for your work!" % (tasks_completed,
                                                                   round(wait_minutes, 2)))
        log_message = "Paying %f x %f + %f x %d - %f = %f dollars to %s for %s" % (
            waiting_rate, wait_minutes, task_rate, tasks_completed,
            list_rate, total_bonus, self.worker, self)
        return (round(max(0.00, total_bonus), 2), worker_message, log_message)


    # Fields related to tracking the worker's wait time
    ###################################################

    # Is this worker on reserve in the pool (i.e., not receiving work)
    on_reserve = models.BooleanField(default=False)

    # Is this worker being let go from the pool?
    worker_released_at = models.DateTimeField(null=True)

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

    @property
    def is_active(self):
        time_cutoff = timezone.now() - timedelta(
            seconds=settings.PING_TIMEOUT_SECONDS)
        return self.last_ping and self.last_ping >= time_cutoff

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

    # The time at which the pool was terminated
    finished_at = models.DateTimeField(null=True)

    # Number of workers desired in the pool
    capacity = models.IntegerField()

    # External identifier for this pool (for re-use)
    external_id = models.CharField(max_length=200, unique=True)

    # Number of times this pool has been churned
    num_churns = models.IntegerField(default=0)

    # Average worker speed in this pool
    mean_speed = models.FloatField(null=True)
    mean_speed_std = models.FloatField(null=True)

    def save(self, *args, **kwargs):
        models.Model.save(self, *args, **kwargs)
        if self.external_id == '':
            self.external_id = str(self.id)
            self.save()

    # Current estimated cost of paying for all tasks/workers in the pool so far.
    def cost(self, waiting_rate, task_rate, list_rate, assignment_model):
        assignments = (
            assignment_model.objects
            .filter(
                task__group__retainer_pool=self,
                task__task_type='retainer'))
        return sum([a.compute_bonus(waiting_rate, task_rate, list_rate)[0] + list_rate 
                    for a in assignments.iterator()])

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
                                   assignments__last_ping__gte=time_cutoff,
                                   assignments__on_reserve=False)

    @property
    def reserve_workers(self):
        time_cutoff = timezone.now() - timedelta(
            seconds=settings.PING_TIMEOUT_SECONDS)
        return self.workers.filter(assignments__task__task_type='retainer',
                                   assignments__task__group__retainer_pool=self,
                                   assignments__last_ping__gte=time_cutoff,
                                   assignments__on_reserve=True)


    # Compute average speed of active workers
    def compute_speed(self, logger=None):
        worker_means = [w.mean_speed for w in self.active_workers if w.mean_speed is not None]
        if len(worker_means) == 0:
            if logger:
                logger.info("No worker means: can't compute pool speed for %s" % self)
            return

        self.mean_speed = np.mean(worker_means)
        self.mean_speed_std = np.std(worker_means)
        self.save()

    def expired_tasks(self, task_model):
        time_cutoff = timezone.now() - timedelta(
            seconds=settings.RETAINER_WORKER_TIMEOUT_SECONDS)
        return (task_model.objects.filter(
                task_type='retainer',
                group__retainer_pool=self)
                .exclude(assignments__isnull=True)
                .exclude(assignments__last_ping__gte=time_cutoff))

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
