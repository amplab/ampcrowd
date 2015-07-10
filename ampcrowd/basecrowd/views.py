from django.core.urlresolvers import reverse
from django.db.models import Count, F
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
        # group id is taken
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
                return HttpResponse(json.dumps(wrong_response))

        else:
            try:
                retainer_pool = retainer_pool_model.objects.get(
                    external_id=pool_id)

                # TODO: Make sure this pool is compatible with the new task group
            except retainer_pool_model.DoesNotExist:
                # clean up
                current_group.delete()
                return HttpResponse(json.dumps(wrong_response))
        current_group.retainer_pool = retainer_pool

        # Don't call interface.create_task, the `post_retainer_tasks` celery
        # task will do so.
        # Create the tasks (1 point per task)
        for point_id, point_content in content.iteritems():
            task_id = str(uuid.uuid4()) # generate a random id for this task
            task = model_spec.task_model(
                task_type=configuration['task_type'],
                data=json.dumps({point_id: point_content}),
                create_time=pytz.utc.localize(datetime.now()),
                task_id=task_id,
                group=current_group,
                num_assignments=configuration['num_assignments'],
                is_retainer=True,
            )
            interface.task_pre_save(task)
            task.save()

        # start the work right away if the pool is ready
        if retainer_pool.status in [RetainerPoolStatus.IDLE,
                                    RetainerPoolStatus.ACTIVE]:
            current_group.retainer_pool_status = TaskGroupRetainerStatus.RUNNING
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
        return HttpResponseBadRequest('Invalid task id: ' + context['task_id'])

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
        if (pool.active_workers.filter(worker_id=worker_id).exists()
            and current_worker.assignments.filter(
                task__group_retainer_pool=pool,
                finished_at__isnull=True)
            .exclude(task=current_task).exists()):
            raise ValueError("Can't join pool twice!")
            # TODO: Make this an html page for the user

        retainer_config = json.loads(
            task_group.global_config)['retainer_pool']
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
            current_worker.pools.add(pool)
            assignment.save()
            context.update({
                'wait_time': assignment.time_waited,
                'tasks_completed': current_worker.completed_assignments_for_pool_session(
                        current_task).count(),
                'understands_retainer': current_worker.understands_retainer,
                })

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
        current_task.save()
        gather_answer.delay(current_task.task_id, model_spec)

        # Check if the whole group is done
        group = current_task.group
        if not (group.tasks
                .exclude(task_type='retainer')
                .filter(is_complete=False).exists()):

            # terminate in progress retainer tasks
            (model_spec.assignment_model.objects
             .exclude(task__task_type='retainer')
             .filter(task__group=group,
                     finished_at__isnull=True)
             .update(finished_at=timezone.now(),
                     terminated=True))

    return HttpResponse('ok')  # AJAX call succeded.


# Views related to Retainer Pool tasks
#######################################


@require_POST
@csrf_exempt
def ping(request, crowd_name):
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
            raise ValueError('Retainer must ping with active task id!')

        active_assignment = model_spec.assignment_model.objects.get(
            worker=worker, task_id=active_task_id)
        if active_assignment.terminated:
            terminate_work = True

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
    }
    return HttpResponse(json.dumps(data), content_type='application/json')


@require_GET
def assign_retainer_task(request, crowd_name):
    # get the interface implementation from the crowd name.
    interface, model_spec = CrowdRegistry.get_registry_entry(crowd_name)

    logger.info('Retainer task requested work.')
    context = interface.get_response_context(request)
    interface.require_context(
        context, ['task_id', 'worker_id'],
        ValueError("retainer assignment context missing required keys."))
    task = (model_spec.task_model.objects
            .select_related('group__retainer_pool')
            .get(task_id=context['task_id']))
    group = task.group
    pool = group.retainer_pool
    worker = model_spec.worker_model.objects.get(worker_id=context['worker_id'])
    if pool.status not in (RetainerPoolStatus.ACTIVE, RetainerPoolStatus.REFILLING,
                           RetainerPoolStatus.IDLE):
        return HttpResponse(json.dumps({'start': False}),
                            content_type='application/json')

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
        open_tasks = (
            incomplete_tasks

            # that haven't been assigned to enough workers yet
            .annotate(num_workers=Count('assignments'))
            .filter(num_workers__lt=F('num_assignments')))
        if open_tasks.exists():
            logger.info('Found an unassigned but open task')
            assignment_task = open_tasks.order_by('?')[0]

        # Then, check if there in-progress tasks with enough assignments.
        elif incomplete_tasks.exists():
            exp_config = json.loads(task.group.global_config).get('experimental')
            if exp_config:
                straggler_mitigation = exp_config.get('mitigate_stragglers')
            else:
                straggler_mitigation = False

            if not straggler_mitigation: # only assign tasks that have been abandoned
                # Bad performance characteristics! consider rewriting.
                active_workers = set(pool.active_workers.all())
                abandoned_tasks = [
                    t for t in incomplete_tasks
                    if not set([a.worker for a in t.assignments.select_related('worker').all()]) <= active_workers]

                if abandoned_tasks:
                    logger.info('Found an assigned but abandoned task.')
                    assignment_task = random.choice(abandoned_tasks)
                else:
                    logger.info('All tasks are assigned.')

            # Straggler mitigation
            else:
                logger.info('Assigning to an active task for straggler mitigation.')
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
        })
        logger.info('Linking task to assignment.')
        return HttpResponse(response_data, content_type='application/json')
    else:
        logger.info('No tasks found!')
        return HttpResponse(json.dumps({'start': False}),
                            content_type='application/json')

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

    # Mark open sessions as interrupted so we don't penalize them unfairly.
    (model_spec.assignment_model.objects
     .filter(task__group__retainer_pool=pool,
             task__task_type='retainer')
     .filter(finished_at__isnull=True)
     .update(pool_ended_mid_assignment=True))

    pool.status = RetainerPoolStatus.FINISHED
    pool.finished_at = timezone.now()
    pool.save()
    logger.info("Retainer pool %s finished" % pool)
    return HttpResponse(json.dumps({'status': 'ok'}))

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
