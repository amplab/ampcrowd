from django.core.urlresolvers import reverse
from django.db.models import Count, F
from django.template import RequestContext, TemplateDoesNotExist
from django.template.loader import get_template, select_template
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.http import HttpResponse
from datetime import datetime
from base64 import b64encode
import pytz
import json
import os
import logging
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
            task = model_spec.task_model(
                task_type=configuration['task_type'],
                data=json.dumps({point_id: point_content}),
                create_time=pytz.utc.localize(datetime.now()),
                task_id=point_id,
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
        current_task = model_spec.task_model.objects.get(
            task_id=context['task_id'])
    except model_spec.task_model.DoesNotExist:
        raise ValueError('Invalid task id: ' + context['task_id'])

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
        logger.info("IS ACCEPTED:" + str(is_accepted))
        logger.info("CURRENT WORKER: %s" % current_worker)
        logger.info("URL: %s" % request.get_full_path())
        if (current_task.group.retainer_pool.active_workers.filter(
                worker_id=worker_id).exists()
            and current_task not in current_worker.tasks.all()):
            raise ValueError("Can't join pool twice!")
            # TODO: Make this an html page for the user

        if is_accepted:
            current_worker.pools.add(current_task.group.retainer_pool)
            current_task.assigned_at = timezone.now()
            current_task.save()
            context.update({
                'wait_time': current_task.time_waited,
                'tasks_completed': current_worker.completed_tasks_for_pool_session(
                    current_task.group.retainer_pool, current_task).count()})

    # Relate workers and tasks (after a worker accepts the task).
    if is_accepted:
        if not current_worker:
            raise ValueError("Accepted tasks must have an associated worker.")
        if not current_worker.tasks.filter(task_id=current_task.task_id).exists():
            current_worker.tasks.add(current_task)

    # Add task data to the context.
    content = json.loads(current_task.data)
    group_context = json.loads(current_task.group.group_context)
    crowd_config = json.loads(current_task.group.crowd_config)
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
    if model_spec.response_model.objects.filter(
            assignment_id=context['assignment_id']).exists():
        return HttpResponse('Duplicate!')

    # Retrieve the task and worker from the database based on ids.
    current_task = model_spec.task_model.objects.get(task_id=context['task_id'])
    current_worker = model_spec.worker_model.objects.get(
        worker_id=context['worker_id'])

    # Store this response into the database
    current_response = model_spec.response_model(
        task=current_task,
        worker=current_worker,
        content=context['answers'],
        assignment_id=context['assignment_id'])
    interface.response_pre_save(current_response)
    current_response.save()

    # Check if this task has been finished
    # If we've gotten too many responses, ignore.
    if (not current_task.is_complete
        and current_task.responses.count() >= current_task.num_assignments):
        current_task.is_complete = True
        current_task.save()
        gather_answer.delay(current_task.task_id, model_spec)

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
        context, ['task_id', 'worker_id'],
        ValueError("ping context missing required keys."))
    task = model_spec.task_model.objects.get(task_id=context['task_id'])
    worker = model_spec.worker_model.objects.get(worker_id=context['worker_id'])

    # update waiting time
    ping_type = request.POST['ping_type']
    last_ping = task.last_ping
    time_since_last_ping = (now - last_ping).total_seconds()

    # Task started waiting, create a new session
    if ping_type == 'starting':
        task.finish_waiting_session()

    # Task is waiting, increment wait time.
    elif ping_type == 'waiting':
        task.time_waited_session += time_since_last_ping

    # Task is working, do nothing.
    elif ping_type == 'working':
        pass

    task.last_ping = now
    task.last_ping_type = ping_type
    task.save()
    worker.last_ping = now
    worker.save()
    logger.info('ping from worker %s, task %s' % (worker, task))

    data = {
        'ping_type': ping_type,
        'wait_time': task.time_waited,
        'tasks_completed': worker.completed_tasks_for_pool_session(
            task.group.retainer_pool, task).count(),
        'pool_status': task.group.retainer_pool.get_status_display(),
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
    task = model_spec.task_model.objects.get(task_id=context['task_id'])
    worker = model_spec.worker_model.objects.get(worker_id=context['worker_id'])
    pool = task.group.retainer_pool

    # Look for a task the worker is already assigned to
    assignment_task = None
    existing_assignments = (worker.tasks
                            .filter(is_complete=False)
                            .filter(group__retainer_pool=pool)
                            .exclude(task_type='retainer'))
    if existing_assignments.exists():
        assignment_task = existing_assignments[0]
    else:  # Look for open tasks
        open_tasks = (

            # incomplete tasks
            model_spec.task_model.objects.filter(is_complete=False)

            # in this pool's tasks
            .filter(group__in=pool.task_groups.all())

            # that aren't dummy retainer tasks
            .exclude(task_type='retainer')

            # that the worker hasn't worked on already
            .exclude(responses__worker=worker)

            # that haven't been assigned to enough workers yet
            .annotate(num_workers=Count('workers'))
            .filter(num_workers__lt=F('num_assignments')))


        # Pick a random one and assign it to the worker
        if open_tasks.exists():
            assignment_task = open_tasks.order_by('?')[0]
            worker.tasks.add(assignment_task)

    # return a url to the assignment
    if assignment_task:
        url_args = {
            'crowd_name': crowd_name,
            'worker_id': worker.worker_id,
            'task_id': assignment_task.task_id,
        }
        response_data = json.dumps({
            'start': True,
            'task_url': reverse('basecrowd:get_retainer_assignment',
                                kwargs=url_args)
        })
        return HttpResponse(response_data, content_type='application/json')
    else:
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

    # construct assignment id
    context = {
        'task_id': task_id,
        'worker_id': worker_id,
        'is_accepted': True,
        'assignment_id': uuid.uuid4()
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

    pool.status = RetainerPoolStatus.FINISHED
    pool.save()
    logger.info("Retainer pool %s finished" % pool)
    return HttpResponse(json.dumps({'status': 'ok'}))
