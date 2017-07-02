import copy
from datetime import datetime, timedelta
import logging
import json
import numpy as np
import urllib
import urllib2

from celery.utils.log import get_task_logger
from celery import group
from celery import shared_task
from django.db.models import F, Max
from django.conf import settings
from django.utils import timezone

from basecrowd.interface import CrowdRegistry
from basecrowd.models import TaskGroupRetainerStatus
from basecrowd.models import RetainerPoolStatus
from basecrowd.models import RetainerTask
from quality_control.em import make_em_answer

logger = get_task_logger(__name__)

# Function for gathering results after a task gets enough votes from the crowd
@shared_task(bind=True, ignore_result=True, store_errors_even_if_ignored=True)
def gather_answer(self, current_task_id, model_spec):
    try:
        pre_em = timezone.now()
        current_task = model_spec.task_model.objects.select_related('group__retainer_pool').get(task_id=current_task_id)
        current_task.pre_em = pre_em
        current_task.save()
        logger.info('Computing EM answer for %s' % current_task)
#        current_task.em_answer = make_em_answer(current_task, model_spec)
        current_task.post_em = timezone.now()
        current_task.save()
        logger.info('Sending Response for %s' % current_task)
        submit_callback_answer(current_task, model_spec)
        current_task.post_celery = timezone.now()
        current_task.save()
    except Exception as exc:
        raise self.retry(exc=exc, countdown=0.1)

# Submit the answers to the callback URL
def submit_callback_answer(current_task, model_spec):
    url = current_task.group.callback_url
#    current_em_answer = json.loads(current_task.em_answer)
    current_em_answer = json.loads(
        current_task.assignments.exclude(content__isnull=True).exclude(content__exact='').exclude(task__task_type='retainer')[0].content)
    logger.info('Loaded answer for %s' % current_task)
    group = current_task.group
    pool = group.retainer_pool
    group_start_time = group.work_start_time.isoformat()

    config = json.loads(group.global_config)
    exp_config = config.get('experimental')
    retainer_config = config.get('retainer_pool')
    if exp_config:
        churn_thresh = exp_config.get('churn_threshold')
    else:
        churn_thresh = None

    json_answer = {'group_id': current_task.group.group_id}
    json_answer['group_start_time'] = group_start_time
    json_answer['answers'] = []

    if pool:
        pool_cost = pool.cost(
            retainer_config['waiting_rate'], retainer_config['task_rate'], retainer_config['list_rate'],
            model_spec.assignment_model)
        pool_mean, pool_std = (pool.mean_speed, pool.mean_speed_std) if pool.mean_speed else (12.00, 0.00)
        logger.info("Computed pool cost and speed for %s" % current_task)

    for key in current_em_answer.keys():
        answer_data = {
            'identifier': key,
            'value': float(current_em_answer[key])
        }
        if pool:
            answer_data['mean_pool_speed'] = pool_mean
            answer_data['pool_speed_std'] = pool_std
            answer_data['num_churns'] = pool.num_churns
            answer_data['pool_cost'] = pool_cost

        json_answer['answers'].append(answer_data)

    # Send back data using urllib2
    logger.info("Built answers for %s" % current_task)
    params = {'data' : json.dumps(json_answer)}
    urllib2.urlopen(url, urllib.urlencode(params))

    # Mark the task as finished
    logger.info("Sent answers for %s" % current_task)
    group.tasks_finished = F('tasks_finished') + 1
    group.save()

# Recruit for retainer pools by auto-posting tasks as necessary.
# TODO: worry about concurrency if multiple of these run at once.
@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def post_retainer_tasks():

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
            
            group = pool.task_groups.order_by('created_at')[0]
            exp_config = json.loads(group.global_config).get('experimental')
            if exp_config:
                churn_thresh = exp_config.get('churn_threshold')
            else:
                churn_thresh = None

            # Check if the pool needs more workers.
            num_active_workers = pool.active_workers.count()
            need_more_active = num_active_workers < pool.capacity
            logging.info("%s needs workers? %s" % (pool, need_more_active))

            num_reserve_workers = pool.reserve_workers.count()
            need_more_reserve = (churn_thresh is not None
                                 and num_reserve_workers < settings.CHURN_RESERVE_SIZE)
            logging.info("%s needs more reserve? %s" % (pool, need_more_reserve))
            

            # if a pool has finished recruiting, start tasks appropriately
            if (pool.status in (RetainerPoolStatus.RECRUITING, RetainerPoolStatus.REFILLING)
                and not need_more_active):

                logger.info("%s is done recruiting" % pool)
                waiting_task_groups = crowd_model_spec.group_model.objects.filter(
                    retainer_pool=pool,
                    retainer_pool_status__in=(TaskGroupRetainerStatus.WAITING,
                                              TaskGroupRetainerStatus.RUNNING))
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

        
            # Create new recruitment tasks if necessary
            elif need_more_active or need_more_reserve:
                logger.info("Posting tasks for %s" % pool)
                
                if (pool.status in (RetainerPoolStatus.ACTIVE, RetainerPoolStatus.IDLE)
                    and need_more_active):
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
                        group=pool.task_groups.order_by('-created_at')[0],
                        num_assignments=task_config['num_assignments'],
                    )
                    logger.info("Created Task %s" % task_id)

                    # Create the retainer task to remember it.
                    retainer_task = RetainerTask.objects.create(
                        task=task, crowd_name=crowd_name)
                    logger.info("Created %s" % retainer_task)
            else:
                logger.info("%s has status %s, nothing to do." % (pool, pool.get_status_display()))

    # Delete old retainerTasks to keep the listings fresh
    logger.info('Removing old retainer tasks...')
    for retainer_task in RetainerTask.objects.filter(active=True).select_related(
        'task__group__retainer_pool'):

        # Did we already process this retainer_task?
        session_task = retainer_task.task
        if not session_task:
            continue

        # Make sure we're actually recruiting
        group = session_task.group
        retainer_pool = group.retainer_pool

        # Always kill off recruitment tasks for finished pools
        pool_finished = (retainer_pool.status == RetainerPoolStatus.FINISHED)

        exp_config = json.loads(group.global_config).get('experimental')
        if exp_config:
            churn_thresh = exp_config.get('churn_threshold')
        else:
            churn_thresh = None
        num_reserve_workers = retainer_pool.reserve_workers.count()
        need_more_reserve = (churn_thresh is not None
                             and num_reserve_workers < settings.CHURN_RESERVE_SIZE)

        not_recruiting = (retainer_pool.status not in (RetainerPoolStatus.RECRUITING, 
                                                       RetainerPoolStatus.REFILLING)
                          and not need_more_reserve) or pool_finished
        if not_recruiting:
            # reset the last_recruited timestamp in case we start recruiting again.
            retainer_pool.last_recruited_at = timezone.now() - timedelta(
                seconds=settings.RETAINER_TASK_EXPIRATION_SECONDS)
            retainer_pool.save()

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
                    try:
                        interface.expire_tasks([session_task,])
                    except Exception as e:
                        logger.exception("Couldn't expire task %s, ignoring..." % session_task)
                    logger.info("Expired platform task %s" % session_task.task_id)
                    retainer_task.active = False
                    retainer_task.save()

                else:
                    logger.info("Not deleting %s, it has a worker." % session_task)

            except Exception, e:
                logger.warning('Could not remove task %s: %s' % (session_task, str(e)))

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def retire_workers():

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
                    if (assignment.is_active or 
                        (assignment.last_ping is None
                         and pool.status != RetainerPoolStatus.FINISHED)):
                        success = False # come back to this assignment later.
                        continue # assignment hasn't been finished yet.

                    logger.info("Proccessing assignment %s" % assignment)
                    worker = assignment.worker
                    assignment.finished_at = timezone.now()
                    assignment.finish_waiting_session()
                    wait_time = assignment.time_waited
                    logger.info("%s waited %f seconds in this session." %
                                (worker, wait_time))

                    # Count tasks the worker has completed during this session.
                    work_assignments = worker.assignments_for_pool_session(expired_task)
                    num_tasks = work_assignments.count()
                    completed_assignments = worker.completed_assignments_for_pool_session(
                        expired_task)
                    num_completed_tasks = completed_assignments.count()
                    logger.info("%s completed %d tasks." % (worker,
                                                            num_completed_tasks))

                    # Make sure the worker has completed the required number of tasks
                    reject = False
                    pay = True
                    group_config = json.loads(expired_task.group.global_config)
                    retain_config = group_config['retainer_pool']

                    not_enough_tasks = (num_completed_tasks < retain_config['min_tasks_per_worker']
                                        and num_tasks > 0)
                    if not_enough_tasks:
                        logger.info("%s was assigned tasks, but didn't compelete the minimum."
                                     % worker)
                        if assignment.pool_ended_mid_assignment:
                            logger.info("work by %s was cut off by the end of the pool, "
                                        "so not rejecting %s." % (worker, assignment))
                        elif assignment.worker_released_at is not None:
                            logger.info("work by %s was cut off by churn, so not "
                                        " rejecting %s." % (worker, assignment))
                        else:
                            logger.info("Work wasn't cut off by pool, will reject.")
                            reject = True
                            pay = False
                    else:
                        logger.info("%s completed enough tasks or wasn't assigned any."
                                    % worker)

                    # Reject the work if they haven't
                    if reject and assignment.rejected_at is None:
                        logger.info("Rejecting %s." % assignment)
                        try:
                            logger.warning("WOULD REJECT %s, BUT BEING NICE" % assignment)
                            #crowd_interface.reject_task(
                            #    assignment, worker, "You must complete at least %d "
                            #    "tasks to be approved for this assignment, as stated "
                            #    "in the HIT instructions." %
                            #    retain_config['min_tasks_per_worker'])
                            assignment.rejected_at = timezone.now()
                        except Exception, e:
                            logger.error('Error rejecting %s' % assignment)
                            success = False
                    elif assignment.rejected_at is not None:
                        logger.info("%s already rejected, no need to reject twice." % assignment)
                    else:
                        logger.info("%s was valid, and doesn't need rejection." % assignment)
                            
                    # Pay the worker if we haven't already.
                    if pay and assignment.paid_at is None and assignment.rejected_at is None:
                        waiting_rate = retain_config['waiting_rate']
                        per_task_rate = retain_config['task_rate']
                        list_rate = retain_config['list_rate']
                        bonus_amount, message, log_msg = assignment.compute_bonus(
                            waiting_rate, per_task_rate, list_rate, logger)
                        logger.info(log_msg)
                        try:
                            if bonus_amount <= 0.01:
                                logger.info("Bonus amount is too small--not giving bonus.")
                            else:
                                crowd_interface.pay_worker_bonus(
                                    worker, assignment, bonus_amount, message)
                                assignment.amount_paid_bonus = bonus_amount
                                assignment.amount_paid_list = list_rate
                                assignment.paid_at = timezone.now()
                        except Exception, e:
                            logger.error('Error paying for %s' % assignment)
                            success = False

                            # payment is important--make it explicit when there's an issue
                            with open('PAYMENT_ERRORS.txt', 'a') as f:
                                print >>f, str(assignment), str(e)
                    elif assignment.paid_at is not None:
                        logger.info("%s already paid, no need to pay twice." % assignment)
                    else:
                        logger.info("%s was rejected, not paying worker." % assignment)
                    assignment.save()                            

                # Mark the task as expired so we don't process it again.
                if success:
                    expired_task.is_retired = True
                    expired_task.save()

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def churn_workers():
    # Process each installed crowd.
    registry = CrowdRegistry.get_registry()
    for crowd_name, (crowd_interface, crowd_model_spec) in registry.iteritems():

        # Skip crowds that don't support retainer pools.
        if not crowd_model_spec.retainer_pool_model:
            logger.info("Crowd %s doesn't support retainer pools, not churning "
                        "workers" % crowd_name)
            continue

        # Find pools that support churn
        logger.info("Crowd %s supports retainer pools, looking for workers to "
                    "churn." % crowd_name)
        valid_states = (RetainerPoolStatus.IDLE, RetainerPoolStatus.ACTIVE,
                        RetainerPoolStatus.REFILLING)
        for pool in crowd_model_spec.retainer_pool_model.objects.filter(
                status__in=valid_states):
            group = pool.task_groups.order_by('created_at')[0]
            exp_config = json.loads(group.global_config).get('experimental')
            if exp_config:
                churn_thresh = exp_config.get('churn_threshold')
            else:
                churn_thresh = None

            # Don't churn pools that don't support churn
            if churn_thresh is None:
                logger.info("Pool doesn't support churn--not churning.")
                continue

            # Don't churn pools if there are no available reserve workers
            num_reserve_workers = pool.reserve_workers.count()
            if num_reserve_workers == 0:
                logger.info("No reserve workers available--not churning.")
                continue
            logger.info("%d workers on reserve--looking for workers to replace." % 
                        num_reserve_workers)

            # Log the pool average
            logger.info("Churning pool %s" % pool)
            if pool.mean_speed:
                logger.info("Average pool speed: %f. std: %f" % (
                        pool.mean_speed, pool.mean_speed_std))

            # Pick the workers above the threshold
            bad_workers = sorted(
                [w for w in pool.active_workers 
                 if w.mean_speed and w.mean_speed - w.mean_speed_ci > churn_thresh],
                reverse=True,
                key=lambda w:w.mean_speed - w.mean_speed_ci)
            num_bad_workers = len(bad_workers)
            logger.info("Found %d workers above the threshold" % num_bad_workers)
            if num_bad_workers == 0:
                logger.info("No workers to release--not churning.")
                continue

            # Perform the churn
            n_to_churn = min(num_bad_workers, num_reserve_workers)
            logger.info("Churning %d workers" % n_to_churn)

            workers_to_churn = bad_workers[:n_to_churn]
            workers_to_add = pool.reserve_workers.order_by('?')[:n_to_churn]
            assert len(workers_to_churn) == len(workers_to_add)
            
            # Activate workers on reserve
            logger.info("Activating reserve workers: %s" % workers_to_add)
            (crowd_model_spec.assignment_model.objects
             .filter(worker__in=workers_to_add)
             .update(on_reserve=False))

            # Release slow workers
            now = timezone.now()
            logger.info("Releasing active workers: %s" % workers_to_churn)
            (crowd_model_spec.assignment_model.objects
             .filter(worker__in=workers_to_churn)
             .update(worker_released_at=now))

            pool.num_churns += 1
            pool.save()
            logger.info('Churn complete.')

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def compute_speeds():
    # Process each installed crowd.
    registry = CrowdRegistry.get_registry()
    for crowd_name, (crowd_interface, crowd_model_spec) in registry.iteritems():

        # Skip crowds that don't support retainer pools.
        if not crowd_model_spec.retainer_pool_model:
            logger.info("Crowd %s doesn't support retainer pools, not computing worker speeds."
                        % crowd_name)
            continue

        # Compute stats needed for speed computation
        s = compute_speed_stats.si(crowd_model_spec)

        # Find pools that are still active
        pool_tasks =[]
        logger.info("Crowd %s supports retainer pools, computing worker speeds."
                    % crowd_name)
        valid_states = (RetainerPoolStatus.IDLE, RetainerPoolStatus.ACTIVE,
                        RetainerPoolStatus.REFILLING)
        for pool in crowd_model_spec.retainer_pool_model.objects.filter(
                status__in=valid_states):
            logger.info("Setting up subtasks for speed computation of %s" % pool)

            # Compute all the individual worker speeds as subtasks
            g = group([compute_worker_speed.si(w.worker_id, crowd_model_spec) 
                       for w in pool.active_workers])

            # Then aggregate them to compute the pool speed
            pool_tasks.append(g | compute_pool_speed.si(pool.id, crowd_model_spec))

        # Execute all of our subtasks
        (s | group(pool_tasks))()

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def compute_speed_stats(model_spec):
    logger.info("Computing stats for speed over all pools...")

    # Finalize times for completed groups
    groups = model_spec.group_model.objects.filter(finished_at__isnull=True)
    for group in groups:
        if not (group.tasks
                .exclude(task_type='retainer')
                .filter(is_complete=False).exists()):
            group.finished_at = (group.tasks
                                 .exclude(task_type='retainer')
                                 .aggregate(end=Max('assignments__finished_at')))['end']
            (group.tasks
             .exclude(task_type='retainer')
             .filter(assignments__finished_at=group.finished_at)
             .update(is_last=True))
            group.save()

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def compute_worker_speed(worker_id, model_spec):
    logger.debug("Compute Worker Speed: %s" % worker_id)
    worker = model_spec.worker_model.objects.get(worker_id=worker_id)
    logger.info("Computing speeds for %s" % worker)
    worker.compute_speed(4, model_spec, logger=logger, alpha=1)

@shared_task(ignore_result=True, store_errors_even_if_ignored=True)
def compute_pool_speed(pool_id, model_spec):
    pool = model_spec.retainer_pool_model.objects.get(id=pool_id)
    logger.info("Finalizing speeds for %s" % pool)
    pool.compute_speed(logger=logger)

