from django.core.files import locks
from django.core.urlresolvers import reverse
from django.db.models import Count, F, Q, Min
from django.template import RequestContext, TemplateDoesNotExist
from django.template.loader import get_template, select_template
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.http import HttpResponse, Http404, HttpResponseBadRequest
from datetime import datetime
from base64 import b64encode
import pytz
import json
import os
import logging
import random
import uuid
import numpy as np

from basecrowd.interface import CrowdRegistry
from basecrowd.models import TaskGroupRetainerStatus
from basecrowd.models import RetainerPoolStatus
from basecrowd.tasks import gather_answer

logger = logging.getLogger('crowd_server')

@require_POST
@csrf_exempt
def create_task_group(request, crowd_name):
    """ See README.md for API. """

    # get the interface implementation from the crowd name.
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)

    # Response dictionaries
    correct_response = {'status': 'ok'}
    wrong_response = {'status': 'wrong'}

    # Parse information contained in the URL
    json_dict = request.POST.get('data')

    # Validate the format.
    if not interface.validate_create_request(json_dict):
        wrong_response['reason'] = 'Invalid request data.'
        return HttpResponse(json.dumps(wrong_response))

    # Pull out important data fields
    json_dict = json.loads(json_dict)
    configuration = json_dict['configuration']
    group_id = json_dict['group_id']
    group_context = json.dumps(json_dict['group_context'])
    content = json_dict['content']
    point_identifiers = content.keys()

    # Create a new group for the tasks.
    if model_spec.group_model.objects.filter(group_id=group_id).exists():
        wrong_response['reason'] = 'Group id %s is already in use.' % group_id
        return HttpResponse(json.dumps(wrong_response))
    current_group = model_spec.group_model(
        group_id=group_id,
        tasks_finished=0,
        callback_url=configuration['callback_url'],
        group_context=group_context,
        crowd_config=json.dumps(configuration.get(crowd_name, {})),
        global_config=json.dumps(configuration))

    # Call the group hook function, then save the new group to the database.
    interface.group_pre_save(current_group)
    current_group.save()

    # Build crowd tasks from the group
    if 'retainer_pool' in configuration: # Retainer pool tasks

        # The specified crowd must support retainer pools
        retainer_pool_model = model_spec.retainer_pool_model
        if not retainer_pool_model:
            wrong_response['reason'] = 'Crowd does not support retainer pools.'
            return HttpResponse(json.dumps(wrong_response))

        # Create or find the retainer pool.
        retainer_config = configuration['retainer_pool']
        create_pool = retainer_config['create_pool']
        pool_id = retainer_config.get('pool_id', '')
        if create_pool:
            (retainer_pool, created) = retainer_pool_model.objects.get_or_create(
                external_id=pool_id,
                defaults={
                    'capacity': retainer_config['pool_size'],
                    'status': RetainerPoolStatus.RECRUITING,
                })
            if created == False: # pool id already taken
                wrong_response['reason'] = 'Pool id %s already in use' % pool_id
                return HttpResponse(json.dumps(wrong_response))

        else:
            try:
                retainer_pool = retainer_pool_model.objects.get(
                    external_id=pool_id)

                # TODO: Make sure this pool is compatible with the new task group
            except retainer_pool_model.DoesNotExist:
                # clean up
                current_group.delete()
                wrong_response['reason'] = 'Pool %s does not exist' % pool_id
                return HttpResponse(json.dumps(wrong_response))
        current_group.retainer_pool = retainer_pool

        # Don't call interface.create_task, the `post_retainer_tasks` celery
        # task will do so.
        # Batch and create the tasks.
        batch_size = configuration['task_batch_size']
        for i in range(0, len(point_identifiers), batch_size):
            batch_point_ids = point_identifiers[i:i+batch_size]
            batch_content = { j: content[j] for j in batch_point_ids }
            task_id = str(uuid.uuid4()) # generate a random id for this task
            task = model_spec.task_model(
                task_type=configuration['task_type'],
                data=json.dumps(batch_content),
                create_time=timezone.now(),
                task_id=task_id,
                group=current_group,
                num_assignments=configuration['num_assignments'],
                is_retainer=True,
                )
            interface.task_pre_save(task)
            task.save()

        #for point_id, point_content in content.iteritems():
        #    task_id = str(uuid.uuid4()) # generate a random id for this task
        #    task = model_spec.task_model(
        #        task_type=configuration['task_type'],
        #        data=json.dumps({point_id: point_content}),
        #        create_time=pytz.utc.localize(datetime.now()),
        #        task_id=task_id,
        #        group=current_group,
        #        num_assignments=configuration['num_assignments'],
        #        is_retainer=True,
        #    )
        #    interface.task_pre_save(task)
        #    task.save()

        # start the work right away if the pool is ready
        if retainer_pool.status in [RetainerPoolStatus.IDLE,
                                    RetainerPoolStatus.ACTIVE]:
            current_group.retainer_pool_status = TaskGroupRetainerStatus.RUNNING
            retainer_pool.status = RetainerPoolStatus.ACTIVE
            retainer_pool.save()
        else:
            current_group.retainer_pool_status = TaskGroupRetainerStatus.WAITING
        current_group.save()

    else: # Not retainer, create a task for each batch of points.
        for i in range(0, len(point_identifiers),
                       configuration['task_batch_size']):

            # build the batch
            current_content = {}
            for j in range(i, i + configuration['task_batch_size']):

                if j >= len(point_identifiers):
                    break
                current_content[point_identifiers[j]] = content[
                    point_identifiers[j]]
            current_content = json.dumps(current_content)

            # Call the create task hook
            current_task_id = interface.create_task(configuration,
                                                    current_content)

            # Build the task object
            current_task = model_spec.task_model(
                task_type=configuration['task_type'],
                data=current_content,
                create_time=pytz.utc.localize(datetime.now()),
                task_id=current_task_id,
                group=current_group,
                num_assignments=configuration['num_assignments'])

            # Call the pre-save hook, then save the task to the database.
            interface.task_pre_save(current_task)
            current_task.save()

    return HttpResponse(json.dumps(correct_response))


# Delete all tasks from the system.
def purge_tasks(request, crowd_name):
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
    tasks = model_spec.task_model.objects.all()

    # Call the delete hook, then delete the tasks from our database.
    # TODO: clean up retainer pool tasks correctly.
    interface.delete_tasks(tasks)
    tasks.delete()
    return HttpResponse('ok')


# we need this view to load in AMT's iframe, so disable Django's built-in
# clickjacking protection.
@xframe_options_exempt
@require_GET
def get_assignment(request, crowd_name):
    # get the interface implementation from the crowd name.
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
    logger.info('Non-retainer worker requested task assignment.')

    # get assignment context
    context = interface.get_assignment_context(request)
    try:
        interface.require_context(
            context, ['task_id', 'is_accepted'],
            ValueError('Task id unavailable in assignment request context.'))
    except ValueError:
        # This task is no longer available (due to a race condition).
        # Return the 'No available tasks' template.
        template = get_scoped_template(crowd_name, 'unavailable.html')
        return HttpResponse(template.render(RequestContext(request, {})))

    return _get_assignment(request, crowd_name, interface, model_spec, context)


def _get_assignment(request, crowd_name, interface, model_spec, context,
                    **custom_template_context):
    # Retrieve the task based on task_id from the database
    try:
        current_task = (model_spec.task_model.objects
                        .select_related('group')
                        .get(task_id=context['task_id']))
        task_group = current_task.group
    except model_spec.task_model.DoesNotExist:
        response_str = '''
        <html><head></head><body>
          <h1>Error: Expired Task!</h1>
          <p>Task %s has expired, and isn't currently available for work.
             <b>Please return this task</b> and pick up a new one.</p>
        </body></html>
        ''' % context['task_id']
        return HttpResponse(response_str)

    # Save the information of this worker
    worker_id = context.get('worker_id')
    if worker_id:
        try:
            current_worker = model_spec.worker_model.objects.get(
                worker_id=worker_id)
        except model_spec.worker_model.DoesNotExist:
            current_worker = model_spec.worker_model(
                worker_id=worker_id)

            # Call the pre-save hook, the save to the database
            interface.worker_pre_save(current_worker)
            current_worker.save()
    else:
        current_worker = None

    is_accepted = context.get('is_accepted', False)

    # If this is a retainer task, add the worker to the pool (if the worker
    # isn't already in the pool, i.e., they're trying to accept multiple HITs
    # for the same pool).
    if current_task.task_type == 'retainer':

        # TODO: consider making this all pools (i.e., a worker can't be in
        # more than one pool at a time).
        pool = task_group.retainer_pool
        if ((pool.active_workers.filter(worker_id=worker_id).exists()
             or pool.reserve_workers.filter(worker_id=worker_id).exists())
            and (current_worker.assignments.filter(
                    task__group__retainer_pool=pool,
                    task__task_type='retainer')
                 .exclude(task=current_task).exists())):
            response_str = '''
            <html><head></head><body>
              <h1>Error: Multiple pool memberships detected</h1>
              <p>You can't accept more than one retainer task at a time,
                 and we've detected that you are already active in another
                 retainer task.</p>
              <p>Please return this task, or leave the pool in your other
                 active task.</p>
              <p><b>Note:</b> You may see this error if you have recently
                 finished another retainer task. In that case, simply wait 5-10
                 seconds and refresh this page, and the error should be gone.
              </p>
            </body></html>
            '''
            return HttpResponse(response_str)

        global_config = json.loads(task_group.global_config)
        retainer_config = global_config['retainer_pool']
        exp_config = global_config.get('experimental')
        churn_thresh = exp_config.get('churn_threshold') if exp_config else None
        context.update({
            'waiting_rate': retainer_config['waiting_rate'],
            'per_task_rate': retainer_config['task_rate'],
            'min_required_tasks': retainer_config['min_tasks_per_worker'],
            'pool_status': pool.get_status_display(),
        })

    # Relate workers and tasks (after a worker accepts the task).
    if is_accepted:
        if not current_worker:
            raise ValueError("Accepted tasks must have an associated worker.")

        assignment_id = context['assignment_id']
        try:
            assignment = current_worker.assignments.get(assignment_id=assignment_id)
        except model_spec.assignment_model.DoesNotExist:
            assignment = model_spec.assignment_model.objects.create(
                assignment_id=assignment_id, worker=current_worker,
                task=current_task)

        # Add the new worker to the session task's retainer pool.
        if current_task.task_type == 'retainer':

            # Put the worker on reserve if the pool is full and we're churning
            if pool.active_workers.count() >= pool.capacity and churn_thresh is not None:
                assignment.on_reserve = True
            else:
                assignment.on_reserve = False

            current_worker.pools.add(pool)
            assignment.save()
            context.update({
                'wait_time': assignment.time_waited,
                'tasks_completed': current_worker.completed_assignments_for_pool_session(
                        current_task).count(),
                'understands_retainer': current_worker.understands_retainer,
                })
        else:
            if not current_task.group.work_start_time:
                current_task.group.work_start_time = timezone.now()
                current_task.group.save()


    # Add task data to the context.
    content = json.loads(current_task.data)
    group_context = json.loads(task_group.group_context)
    crowd_config = json.loads(task_group.crowd_config)
    context.update(group_context=group_context,
                   content=content,
                   backend_submit_url=interface.get_backend_submit_url(),
                   frontend_submit_url=interface.get_frontend_submit_url(crowd_config),
                   crowd_name=crowd_name)
    context.update(**custom_template_context)

    # Load the template and render it.
    template = get_scoped_template(crowd_name, current_task.task_type + '.html',
                            context=context)
    return HttpResponse(template.render(RequestContext(request, context)))


def get_scoped_template(crowd_name, template_name, context=None):
    base_template_name = os.path.join(crowd_name, 'base.html')
    if context is not None:
        try:
            t = get_template(base_template_name)
        except TemplateDoesNotExist:
            base_template_name = 'basecrowd/base.html'
        context['base_template_name'] = base_template_name

    return select_template([
        os.path.join(crowd_name, template_name),
        os.path.join('basecrowd', template_name)])


# When workers submit assignments, we should send data to this view via AJAX
# before submitting to AMT.
@require_POST
@csrf_exempt
def post_response(request, crowd_name):

    # get the interface implementation from the crowd name.
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)

    # get context from the request
    context = interface.get_response_context(request)

    # validate context
    interface.require_context(
        context, ['assignment_id', 'task_id', 'worker_id', 'answers'],
        ValueError("Response context missing required keys."))

    # Check if this is a duplicate response
    assignment_id = context['assignment_id']
    if model_spec.assignment_model.objects.filter(
            assignment_id=assignment_id,
            finished_at__isnull=False).exists():
        return HttpResponse('Duplicate!')

    # Retrieve the task and worker from the database based on ids.
    current_task = model_spec.task_model.objects.get(task_id=context['task_id'])
    assignment = model_spec.assignment_model.objects.get(assignment_id=assignment_id)

    # Store this response into the database
    assignment.content = context['answers']
    assignment.finished_at = timezone.now()
    interface.response_pre_save(assignment)
    assignment.save()

    # Check if this task has been finished
    # If we've gotten too many responses, ignore.
    if (not current_task.is_complete
        and (current_task.assignments.filter(finished_at__isnull=False).count()
             >= current_task.num_assignments)):
        current_task.is_complete = True
        current_task.pre_celery = timezone.now()
        current_task.save()
        gather_answer.delay(current_task.task_id, model_spec)

        # terminate in progress retainer tasks
        (model_spec.assignment_model.objects
         .exclude(task__task_type='retainer')
         .filter(task=current_task,
                 finished_at__isnull=True)
         .update(finished_at=timezone.now(),
                 terminated=True))

    return HttpResponse('ok')  # AJAX call succeded.


# Views related to Retainer Pool tasks
#######################################


@require_POST
@csrf_exempt
def ping(request, crowd_name):
    try:
        interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
        now = timezone.now()

        # get and validate context
        context = interface.get_response_context(request)
        interface.require_context(
            context, ['task_id', 'worker_id', 'assignment_id'],
            ValueError("ping context missing required keys."))
        task = model_spec.task_model.objects.get(task_id=context['task_id'])
        worker = model_spec.worker_model.objects.get(worker_id=context['worker_id'])
        assignment = model_spec.assignment_model.objects.get(
            assignment_id=context['assignment_id'])
        pool_status = task.group.retainer_pool.get_status_display()
        terminate_work = False
        terminate_worker = assignment.worker_released_at is not None

        # update waiting time
        ping_type = request.POST['ping_type']

        # Task started waiting, create a new session
        if ping_type == 'starting':
            assignment.finish_waiting_session()

        # Task is waiting, increment wait time.
        elif ping_type == 'waiting' and pool_status != 'finished':
            last_ping = assignment.last_ping
            time_since_last_ping = (now - last_ping).total_seconds()
            assignment.time_waited_session += time_since_last_ping

        # Task is working, verify that the assignment hasn't been terminated.
        elif ping_type == 'working':
            active_task_id = request.POST.get('active_task', None)
            if not active_task_id:
                logger.warning('Ping from %s, but no active task id.' % assignment)
                terminate_worker = False # Don't kill them if we don't know what they're working on

            else:
                try:
                    active_assignment = model_spec.assignment_model.objects.filter(
                        worker=worker, task_id=active_task_id)[0]
                    if active_assignment.terminated:
                        terminate_work = True
                except IndexError: # No active assignment
                    terminate_worker = False # Don't kill the worker if we don't know what they're working on.
                    
#                if terminate_worker: # make sure their current task can be recycled
#                    active_assignment.finished_at = now
#                    active_assignment.terminated = True
#                    active_assignment.save()

        assignment.last_ping = now
        assignment.save()
        worker.last_ping = now
        worker.save()
        logger.info('ping from worker %s, task %s' % (worker, task))

        retainer_config = json.loads(task.group.global_config)['retainer_pool']
        data = {
            'ping_type': ping_type,
            'wait_time': assignment.time_waited,
            'tasks_completed': worker.completed_assignments_for_pool_session(
                task).count(),
            'pool_status': pool_status,
            'waiting_rate': retainer_config['waiting_rate'],
            'per_task_rate': retainer_config['task_rate'],
            'min_required_tasks': retainer_config['min_tasks_per_worker'],
            'terminate_work': terminate_work,
            'terminate_worker': terminate_worker,
            }
        return HttpResponse(json.dumps(data), content_type='application/json')
    except Exception as e:
        logger.exception(e)
        raise e


@require_GET
def assign_retainer_task(request, crowd_name):
    try:
        # get the interface implementation from the crowd name.
        interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)

        context = interface.get_response_context(request)
        interface.require_context(
            context, ['task_id', 'worker_id'],
            ValueError("retainer assignment context missing required keys."))

        try:
            task = (model_spec.task_model.objects
                    .select_related('group__retainer_pool')
                    .get(task_id=context['task_id']))
            group = task.group
            pool = group.retainer_pool
            worker = model_spec.worker_model.objects.get(worker_id=context['worker_id'])
            logger.info('Retainer task %s requested work.' % task)
        except Exception: # Issue loading models from IDs, finish this assignment
            return HttpResponse(json.dumps({'start': False, 'pool_status': 'finished'}),
                                content_type='application/json')
        
        exp_config = json.loads(group.global_config).get('experimental')
        if exp_config:
            straggler_mitigation = exp_config.get('mitigate_stragglers', False)
            straggler_routing_policy = exp_config.get('straggler_routing_policy', 'random')
            churn_threshold = exp_config.get('churn_threshold')
        else:
            straggler_mitigation = False
            churn_threshold = None

        # Acquire an exclusive lock to avoid duplicate assignments
        lockf = open('/tmp/ASSIGNMENT_LOCK', 'wb')
        logger.debug("Locking assignment lock...")
        locks.lock(lockf, locks.LOCK_EX)

        # Don't assign a task if the worker is on reserve or the pool is inactive.
        on_reserve = (task.assignments.filter(worker=worker, on_reserve=True).exists()
                      if churn_threshold is not None else False)
        pool_inactive = pool.status not in (RetainerPoolStatus.ACTIVE, 
                                            RetainerPoolStatus.REFILLING,
                                            RetainerPoolStatus.IDLE)
        no_work_response = HttpResponse(json.dumps({'start': False,
                                                    'pool_status': pool.get_status_display()}),
                                        content_type='application/json')
        if on_reserve:
            logger.info("Worker on reserve: not assigning work.")
            return no_work_response

        if pool_inactive:
            logger.info("Pool still recruiting or otherwise inactive: not assigning work.")
            return no_work_response

        # Look for a task the worker is already assigned to
        assignment_task = None
        existing_assignments = (worker.assignments
                                .filter(finished_at__isnull=True)
                                .filter(task__group__retainer_pool=pool)
                                .exclude(task__task_type='retainer'))

        logger.info('Looking for assignments for retainer worker...')
        if existing_assignments.exists():
            assignment_task = existing_assignments[0].task
            logger.info('Found an existing assignment for this worker')
        else:  # Look for open tasks
            incomplete_tasks = (
                
                # incomplete tasks
                model_spec.task_model.objects.filter(is_complete=False)
                
                # in this pool's tasks
                .filter(group__retainer_pool=pool)
                
                # that aren't dummy retainer tasks
                .exclude(task_type='retainer')
                
                # that the worker hasn't worked on already
                .exclude(assignments__worker=worker))

            # First check if the open tasks haven't been assigned to enough workers.
            # TODO: avoid gross SQL
            non_terminated_assignments = """
SELECT COUNT(*) FROM %(crowdname)s_%(assignment_model)s
WHERE %(crowdname)s_%(assignment_model)s.terminated = False
      AND %(crowdname)s_%(assignment_model)s.task_id = %(crowdname)s_%(task_model)s.task_id
""" % {
                'crowdname': crowd_name,
                'assignment_model': model_spec.assignment_model.__name__.lower(),
                'task_model': model_spec.task_model.__name__.lower(),
                }

            open_tasks = incomplete_tasks.extra(
                where=["num_assignments > (%s)" % non_terminated_assignments])
            if open_tasks.exists():
                logger.info('Found an unassigned but open task')
                assignment_task = open_tasks.order_by('?')[0]

            # Then, check if there in-progress tasks with enough assignments.
            elif incomplete_tasks.exists():
                if not straggler_mitigation: # only assign tasks that have been abandoned
                    # Bad performance characteristics! consider rewriting.
                    active_workers = set(pool.active_workers.all())
                    abandoned_tasks = [
                        t for t in incomplete_tasks
                        if len([a for a in t.assignments.select_related('worker').all()
                                if a.worker in active_workers]) < t.num_assignments]
                    if abandoned_tasks:
                        logger.info('Found an assigned but abandoned task.')
                        assignment_task = random.choice(abandoned_tasks)
                    else:
                        logger.info('All tasks are assigned.')

                # Straggler mitigation
                else:
                    logger.info('Assigning to an active task for straggler mitigation with policy %s.' %
                                straggler_routing_policy)
                    if straggler_routing_policy == 'random':
                        assignment_task = incomplete_tasks.order_by('?')[0]
                    elif straggler_routing_policy == 'oldest':
                        now = timezone.now()
                        annotated = incomplete_tasks.annotate(start=Min('assignments__assigned_at'))
                        weights = [(now - t.start).total_seconds() for t in annotated]
                        weights = np.array(weights) / sum(weights)
                        assignment_task = np.random.choice(list(annotated), size=1, p=weights)[0]
                    elif straggler_routing_policy == 'young-workers':
                        now = timezone.now()
                        weights = [
                            1 / (now - min([a.worker.assignments
                                            .filter(task__task_type='retainer',
                                                    task__group__retainer_pool=pool)
                                            .order_by('assigned_at')[0].assigned_at
                                            for a in task.assignments.all()])).total_seconds()
                            for task in incomplete_tasks]
                        weights = np.array(weights) / sum(weights)
                        assignment_task = np.random.choice(list(incomplete_tasks), size=1, p=weights)[0]
                    elif straggler_routing_policy == 'fair':
                        # assign to the task with the fewest assignments
                        assignment_task = (incomplete_tasks
                                           .extra(select={'n_assignments': non_terminated_assignments},
                                                  order_by=['n_assignments']))[0]
                    else:
                        logger.info('Unkown straggler routing policy: %s. Using random instead...' %
                                    straggler_routing_policy)
                        assignment_task = incomplete_tasks.order_by('?')[0]

        # return a url to the assignment
        if assignment_task:
            # create the assignment if necessary
            try:
                logger.info('Looking up assignment...')
                assignment = worker.assignments.get(
                    task=assignment_task, worker=worker)
                if not assignment.retainer_session_task:
                    assignment.retainer_session_task = task
                    assignment.save()
            except model_spec.assignment_model.DoesNotExist:
                logger.info('No assignment found: creating new one.')
                assignment_id = str(uuid.uuid4())
                assignment = model_spec.assignment_model.objects.create(
                    assignment_id=assignment_id, worker=worker,
                    task=assignment_task,
                    retainer_session_task=task)

            if not assignment_task.group.work_start_time:
                assignment_task.group.work_start_time = timezone.now()
                assignment_task.group.save()
            url_args = {
                'crowd_name': crowd_name,
                'worker_id': worker.worker_id,
                'task_id': assignment_task.task_id,
                }
            response_data = json.dumps({
                    'start': True,
                    'task_url': reverse('basecrowd:get_retainer_assignment',
                                        kwargs=url_args),
                    'task_id': assignment_task.task_id,
                    'pool_status': pool.get_status_display()
                    })
            logger.info('Linking task to assignment.')
            return HttpResponse(response_data, content_type='application/json')
        else:
            logger.info('No tasks found!')
            return no_work_response

    except Exception as e:
        logger.exception(e)
        raise e

    finally:
       # Release the assignment lock--either an assignment has been created in the DB, or an error occurred.
        logger.debug("Unlocking assignment lock...")
        locks.unlock(lockf)
        lockf.close()

# we need this view to load in AMT's iframe, so disable Django's built-in
# clickjacking protection.
@xframe_options_exempt
@require_GET
def get_retainer_assignment(request, crowd_name, worker_id, task_id):
    # get the interface implementation from the crowd name.
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
    logger.info('Retainer worker fetched task assignment.')

    # fetch assignment if it already exists (e.g. the user refreshed the browser).
    try:
        assignment_id = model_spec.assignment_model.objects.get(
            task_id=task_id, worker_id=worker_id).assignment_id
    except model_spec.assignment_model.DoesNotExist:
        assignment_id = str(uuid.uuid4())
    context = {
        'task_id': task_id,
        'worker_id': worker_id,
        'is_accepted': True,
        'assignment_id': assignment_id
    }

    return _get_assignment(request, crowd_name, interface, model_spec, context)

@require_POST
@csrf_exempt
def finish_pool(request, crowd_name):
    pool_id = request.POST.get('pool_id')
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
    try:
        pool = model_spec.retainer_pool_model.objects.get(external_id=pool_id)
    except model_spec.retainer_pool_model.DoesNotExist:
        return HttpResponse(json.dumps({'error': 'Invalid pool id'}))
    _finish_pool(pool, model_spec)
    logger.info("Retainer pool %s finished" % pool)
    return HttpResponse(json.dumps({'status': 'ok'}))

def _finish_pool(pool, model_spec):
    # Mark open sessions as interrupted so we don't penalize them unfairly.
    (model_spec.assignment_model.objects
     .filter(task__group__retainer_pool=pool,
             task__task_type='retainer')
     .exclude(Q(finished_at__isnull=False) & Q(terminated=False))
     .update(pool_ended_mid_assignment=True))

    pool.status = RetainerPoolStatus.FINISHED
    pool.finished_at = timezone.now()
    pool.save()

@require_POST
@csrf_exempt
def understands_retainer(request, crowd_name, worker_id):
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)
    try:
        worker = model_spec.worker_model.objects.get(worker_id=worker_id)
    except model_spec.worker_model.DoesNotExist:
        return HttpResponse(json.dumps({'error': 'Invalid worker id'}))

    worker.understands_retainer = True
    worker.save()
    logger.info('%s understands the retainer model.' % worker)

    return HttpResponse(json.dumps({'status': 'ok'}))
