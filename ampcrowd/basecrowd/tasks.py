from datetime import datetime, timedelta
import logging
import json
import urllib
import urllib2

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

                # Post retainer tasks for the pool
                logger.info("Posting tasks for %s" % pool)
                # TODO: actually post HITs
                retainer_task = RetainerTask.objects.create()
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

    # Delete old retainerTasks to keep the listings fresh
    logger.info('Removing old retainer tasks...')
    for retainer_task in RetainerTask.objects.filter(active=True):
        old_task_cutoff = (
            datetime.now()
            - timedelta(settings.RETAINER_TASK_EXPIRATION_SECONDS))
        if retainer_task.created_at < old_task_cutoff:
            try:
                logger.info('Removing old retainer task %s' % retainer_task)
                # TODO: remove task from crowd
                retainer_task.active = False
                retainer_task.save()
            except Exception, e:
                logger.warning('Could not remove %s: %s' % (retainer_task,
                                                            str(e)))
