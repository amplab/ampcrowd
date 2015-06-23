import copy
from datetime import datetime, timedelta
import logging
import json
import urllib
import urllib2

from django.conf import settings
from django.utils import timezone
from djcelery import celery

from basecrowd.interface import CrowdRegistry
from basecrowd.models import TaskGroupRetainerStatus
from basecrowd.models import RetainerPoolStatus
from basecrowd.models import RetainerTask
from quality_control.em import make_em_answer

# Function for gathering results after a task gets enough votes from the crowd
@celery.task
def gather_answer(current_task_id, model_spec):
    current_task = model_spec.task_model.objects.get(task_id=current_task_id)
    current_task.em_answer = make_em_answer(current_task, model_spec)
    current_task.save()
    current_task.group.tasks_finished += 1
    current_task.group.save()
    submit_callback_answer(current_task)


# Submit the answers to the callback URL
def submit_callback_answer(current_task):
    url = current_task.group.callback_url
    json_answer = {'group_id': current_task.group.group_id}
    current_em_answer = json.loads(current_task.em_answer)

    json_answer['answers'] = []
    for key in current_em_answer.keys():
        json_answer['answers'].append({'identifier': key, 'value': current_em_answer[key]})

    # Send back data using urllib2
    params = {'data' : json.dumps(json_answer)}
    urllib2.urlopen(url, urllib.urlencode(params))

# Recruit for retainer pools by auto-posting tasks as necessary.
# TODO: worry about concurrency if multiple of these run at once.
@celery.task
def post_retainer_tasks():
    logger = logging.getLogger(__name__)

    # Process each installed crowd.
    registry = CrowdRegistry.get_registry()
    for crowd_name, (crowd_interface, crowd_model_spec) in registry.iteritems():

        # Skip crowds that don't support retainer pools.
        if not crowd_model_spec.retainer_pool_model:
            logger.info("Crowd %s doesn't support retainer pools, not posting "
                        "tasks." % crowd_name)
            continue

        # Find pools that need more workers.
        logger.info("Crowd %s supports retainer pools, looking for pools that "
                    "need more workers." % crowd_name)
        valid_states = (RetainerPoolStatus.RECRUITING, RetainerPoolStatus.IDLE,
                        RetainerPoolStatus.ACTIVE)
        for pool in crowd_model_spec.retainer_pool_model.objects.filter(
                status__in=valid_states):
            if pool.active_workers.count() < pool.capacity:
                logger.info("Posting tasks for %s" % pool)

                now = timezone.now()
                if (pool.last_recruited_at > now - timedelta(
                        seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS)):
                    logger.info("Pool was recruited recently... skipping.")
                    continue
                pool.last_recruited_at = now
                pool.save()

                # Create dummy tasks on the crowd platform
                dummy_content = json.dumps({})
                group = pool.task_groups.order_by('created_at')[0]
                dummy_config = {
                    'num_assignments': 1,
                    'task_type': 'retainer',
                    'task_batch_size': 1,
                    'callback_url': '',
                    crowd_name: json.loads(group.crowd_config),
                }
                for i in range(1, settings.NUM_RETAINER_RECRUITMENT_TASKS + 1):
                    task_config = copy.deepcopy(dummy_config)
                    task_config[crowd_name]['title'] += " [" + str(i) + "]"
                    task_id = crowd_interface.create_task(task_config, dummy_content)

                    # skip interface.task_pre_save because this isn't a real task.
                    task = crowd_model_spec.task_model.objects.create(
                        task_type=task_config['task_type'],
                        data=dummy_content,
                        create_time=timezone.now(),
                        task_id=task_id,
                        group=pool.task_groups.order_by('created_at')[0],
                        num_assignments=task_config['num_assignments'],
                    )
                    logger.info("Created Task %s" % task_id)

                    # Create the retainer task to remember it.
                    retainer_task = RetainerTask.objects.create(
                        task=task, crowd_name=crowd_name)
                    logger.info("Created %s" % retainer_task)

            # if a pool has finished recruiting, start tasks appropriately
            elif pool.status == RetainerPoolStatus.RECRUITING:

                logger.info("%s is done recruiting" % pool)
                waiting_task_groups = crowd_model_spec.group_model.objects.filter(
                    retainer_pool=pool,
                    retainer_pool_status=TaskGroupRetainerStatus.WAITING)
                if not waiting_task_groups.exists():
                    logger.info("No waiting task groups, pool is idle")
                    pool.status = RetainerPoolStatus.IDLE
                else:
                    logger.info("Waiting task groups found, starting work.")
                    pool.status = RetainerPoolStatus.ACTIVE
                    for task_group in waiting_task_groups:
                        logger.info("%s now running." % task_group)
                        task_group.retainer_pool_status = (
                            TaskGroupRetainerStatus.RUNNING)
                        task_group.save()
                pool.save()

            else:
                logger.info("%s has status %s, nothing to do." % (pool, pool.get_status_display()))

    # Delete old retainerTasks to keep the listings fresh
    logger.info('Removing old retainer tasks...')
    for retainer_task in RetainerTask.objects.filter(active=True):
        old_task_cutoff = (
            timezone.now()
            - timedelta(seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS))
        if retainer_task.created_at < old_task_cutoff:
            try:
                # delete the underlying task object if no one has accepted it.
                if not retainer_task.task.workers.exists():
                    interface, _ = CrowdRegistry.get_registry_entry(
                        retainer_task.crowd_name)
                    interface.delete_tasks([retainer_task.task,])
                    retainer_task.task.delete()
                    logger.info("Deleted old task %s" % retainer_task.task)

                else:
                    logger.info("Not deleting %s, it has a worker."
                                % retainer_task.task)

                # Mark the recruitment task inactive
                retainer_task.active = False
                retainer_task.save()
                logger.info('Deleted old retainer task %s' % retainer_task)

            except Exception, e:
                logger.warning('Could not remove task %s: %s' % (
                    retainer_task.task, str(e)))

@celery.task
def retire_workers():
    logger = logging.getLogger(__name__)

    # Process each installed crowd.
    registry = CrowdRegistry.get_registry()
    for crowd_name, (crowd_interface, crowd_model_spec) in registry.iteritems():

        # Skip crowds that don't support retainer pools.
        if not crowd_model_spec.retainer_pool_model:
            logger.info("Crowd %s doesn't support retainer pools, not expiring "
                        "workers" % crowd_name)
            continue

        # Find pools that need more workers.
        logger.info("Crowd %s supports retainer pools, looking for workers to "
                    "retire." % crowd_name)
        for pool in crowd_model_spec.retainer_pool_model.objects.all():
            for expired_task in pool.new_expired_tasks(crowd_model_spec.task_model):
                logger.info("%s has expired. Cleaning up and paying the "
                            "worker." % expired_task)

                # mark the retainer task as expire
                worker = expired_task.workers.all()[0]
                expired_task.is_retired = True

                # Tally the work done by the worker
                assert expired_task.workers.count() == 1
                expired_task.time_waited_total += expired_task.time_waited_session
                expired_task.time_waited_session = 0
                expired_task.save()
                wait_time = expired_task.time_waited_total

                logger.info("%s waited %f seconds on this task."
                            % (worker, wait_time))

                # Find the tasks the worker has completed during this session in
                # the pool. Avoid double-counting tasks between sessions
                try:
                    next_session_start = expired_task.get_next_by_assigned_at(
                        workers=worker, task_type='retainer')
                except crowd_model_spec.task_model.DoesNotExist:
                    next_session_start = timezone.make_aware(datetime.max, None)
                completed_tasks = (
                    worker.tasks
                    .exclude(task_type='retainer')
                    .filter(group__retainer_pool=pool)

                    # Only tasks that this worker gave answers for within the
                    # time of the current session.
                    .filter(responses__worker=worker,
                            responses__created_at__gte=expired_task.assigned_at,
                            responses__created_at__lte=next_session_start))
                num_completed_tasks = completed_tasks.count()
                logger.info("%s completed %d tasks." % (worker,
                                                        num_completed_tasks))

                # Make sure the worker has completed the required number of tasks
                if num_completed_tasks < 0: # TODO: pass this around config
                    # TODO: reject work
                    pass

                # Pay the worker
                else:
                    waiting_rate = 0 # TODO: load this
                    per_task_rate = 0 # TODO: load this
                    list_rate = 0 # TODO: load this
                    total_owed = (waiting_rate * wait_time
                                  + per_task_rate * num_completed_tasks)
                    bonus_amount = total_owed - list_rate
                    # TODO: make the bonus payment
                    logging.info("Paying %f x %f + %f x %d - %f = %f dollars "
                                 "to %s" % (waiting_rate, wait_time,
                                            per_task_rate, num_completed_tasks,
                                            list_rate, bonus_amount, worker))
