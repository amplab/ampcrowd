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

    json_answer['group_start_time'] = current_task.group.work_start_time.isoformat()

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
                        RetainerPoolStatus.ACTIVE, RetainerPoolStatus.REFILLING)
        for pool in crowd_model_spec.retainer_pool_model.objects.filter(
                status__in=valid_states):
            num_active_workers = pool.active_workers.count()
            if num_active_workers < pool.capacity:
                logger.info("Posting tasks for %s" % pool)
                
                if pool.status in (
                    RetainerPoolStatus.ACTIVE, RetainerPoolStatus.IDLE):
                    pool.status = RetainerPoolStatus.REFILLING

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
            elif pool.status in (RetainerPoolStatus.RECRUITING, RetainerPoolStatus.REFILLING):

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
        # Did we already process this retainer_task?
        session_task = retainer_task.task
        if not session_task:
            continue

        # Make sure we're actually recruiting
        retainer_pool = session_task.group.retainer_pool
        not_recruiting = retainer_pool.status not in (
            RetainerPoolStatus.RECRUITING, RetainerPoolStatus.REFILLING)
        if not_recruiting:
            # reset the last_recruited timestamp in case we start recruiting again.
            retainer_pool.last_recruited_at = timezone.now() - timedelta(
                seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS)
            retainer_pool.save()

        # Always kill off recruitment tasks for finished pools
        pool_finished = (retainer_pool.status == RetainerPoolStatus.FINISHED)

        # Kill off tasks that have been around for too long
        old_task_cutoff = (
            timezone.now()
            - timedelta(seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS))
        if not_recruiting or retainer_task.created_at < old_task_cutoff:
            try:
                was_assigned = session_task.assignments.exists()
                interface, _ = CrowdRegistry.get_registry_entry(
                    retainer_task.crowd_name)
                # delete the crowd platform task if no one has accepted it.
                if not was_assigned:
                    interface.delete_tasks([session_task,])
                    logger.info("Deleted platform task %s" % session_task.task_id)
                    session_task.delete()
                    logger.info("Deleted ampcrowd task object %s" % session_task)
                    retainer_task.active = False
                    retainer_task.save()
                
                # expire the crowd platform task if the pool is finished
                elif pool_finished:
                    interface.expire_tasks([session_task,])
                    logger.info("Expired platform task %s" % session_task.task_id)
                    retainer_task.active = False
                    retainer_task.save()

                else:
                    logger.info("Not deleting %s, it has a worker." % session_task)

            except Exception, e:
                logger.warning('Could not remove task %s: %s' % (session_task, str(e)))

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

        # Find pools with expired workers.
        logger.info("Crowd %s supports retainer pools, looking for workers to "
                    "retire." % crowd_name)
        for pool in crowd_model_spec.retainer_pool_model.objects.all():
            for expired_task in pool.new_expired_tasks(crowd_model_spec.task_model):
                logger.info("%s has expired. Cleaning up and paying the "
                            "workers." % expired_task)
                success = True

                # Tally the work done by workers on this session task
                assignments = expired_task.assignments.all()
                logger.info("%d workers on session %s" %
                            (assignments.count(), expired_task))
                for assignment in assignments:
                    logger.info("Proccessing assignment %s" % assignment)
                    worker = assignment.worker
                    assignment.finish_waiting_session()
                    wait_time = assignment.time_waited
                    logger.info("%s waited %f seconds in this session." %
                                (worker, wait_time))

                    # Count tasks the worker has completed during this session.
                    completed_assignments = worker.completed_assignments_for_pool_session(
                        expired_task)
                    num_completed_tasks = completed_assignments.count()
                    logger.info("%s completed %d tasks." % (worker,
                                                            num_completed_tasks))

                    # Make sure the worker has completed the required number of tasks
                    group_config = json.loads(expired_task.group.global_config)
                    retain_config = group_config['retainer_pool']
                    if (num_completed_tasks < retain_config['min_tasks_per_worker']
                        and assignment.rejected_at is None):
                        logger.info("%s didn't complete enough tasks in the pool, "
                                    "rejecting work." % worker)
                        try:
                            crowd_interface.reject_task(
                                assignment, worker, "You must complete at least %d "
                                "tasks to be approved for this assignment, as stated "
                                "in the HIT instructions." %
                                retain_config['min_tasks_per_worker'])
                            assignment.rejected_at = timezone.now()
                        except Exception, e:
                            logger.error('Error rejecting %s' % assignment)
                            success = False
                    elif assignment.rejected_at is not None:
                        logger.info("%s already rejected, no need to reject twice." % assignment)
                    else:
                        logger.info("%s was valid, and doesn't need rejection." % assignment)
                            
                    # Pay the worker if we haven't already.
                    if assignment.paid_at is None and assignment.rejected_at is None and success:
                        waiting_rate = retain_config['waiting_rate']
                        per_task_rate = retain_config['task_rate']
                        list_rate = retain_config['list_rate']
                        bonus_amount, message = assignment.compute_bonus(
                            waiting_rate, per_task_rate, list_rate, logger)
                        try:
                            crowd_interface.pay_worker_bonus(
                                worker, assignment, bonus_amount, message)
                            assignment.amount_paid_bonus = bonus_amount
                            assignment.amount_paid_list = list_rate
                            assignment.paid_at = timezone.now()
                            assignment.save()
                        except Exception, e:
                            logger.error('Error paying for %s' % assignment)
                            success = False

                            # payment is important--make it explicit when there's an issue
                            with open('PAYMENT_ERRORS.txt', 'a') as f:
                                print >>f, str(assignment)
                    elif assignment.paid_at is not None:
                        logger.info("%s already paid, no need to pay twice." % assignment)
                    else:
                        logger.info("%s was rejected or there was an error, not paying worker." % assignment)


                # Mark the task as expired so we don't process it again.
                if success:
                    expired_task.is_retired = True
                    expired_task.save()
