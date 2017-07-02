import json
import pytz
from datetime import datetime

from django.conf import settings

from basecrowd.interface import CrowdInterface
from connection import create_hit, disable_hit, reject_assignment, bonus_worker, expire_hit
from connection import AMT_NO_ASSIGNMENT_ID
from models import Request


class AMTCrowdInterface(CrowdInterface):

    @staticmethod
    def validate_configuration(configuration):
        # Validate the configuration specific to amt
        try:
            CrowdInterface.require_context(
                configuration,
                ['sandbox'],
                ValueError())

        except ValueError:
            return False

        return True

    @staticmethod
    def create_task(configuration, content):
        # Use the boto API to create an AMT HIT
        additional_options = {'num_responses': configuration['num_assignments']}
        additional_options.update(configuration['amt'])
        return create_hit(additional_options)

    @staticmethod
    def pay_worker_bonus(worker_object, assignment_object, bonus_amount, reason):
        bonus_worker(worker_object, assignment_object, bonus_amount, reason)

    @staticmethod
    def reject_task(assignment_object, worker_object, reason):
        reject_assignment(assignment_object, reason)

    @staticmethod
    def expire_tasks(task_objects):
        for task in task_objects:
            expire_hit(task)

    @staticmethod
    def delete_tasks(task_objects):
        # Use the boto API to delete the HITs
        for task in task_objects:
            disable_hit(task)

    @staticmethod
    def get_assignment_context(request):
        request_data = request.GET if request.method == 'GET' else request.POST

        # parse information from AMT in the URL
        context = {
            'task_id': request_data.get('hitId'),
            'worker_id': request_data.get('workerId'),
            'submit_url': request_data.get('turkSubmitTo'),
        }

        # check for requests for a preview of the task
        assignment_id = request_data.get('assignmentId')
        if assignment_id == AMT_NO_ASSIGNMENT_ID:
            assignment_id = None
            is_accepted = False
        else:
            is_accepted = True
        context['assignment_id'] = assignment_id
        context['is_accepted'] = is_accepted

        # store the request if it has been accepted
        if is_accepted:
            Request.objects.create(
                path=request.get_full_path(),
                post_json=json.dumps(dict(request.GET.items() + request.POST.items())),
                recv_time=pytz.utc.localize(datetime.now()))

        return context

    def get_frontend_submit_url(self, crowd_config):
        return (settings.POST_BACK_AMT_SANDBOX
                if crowd_config['sandbox'] else settings.POST_BACK_AMT)

AMT_INTERFACE = AMTCrowdInterface('amt')
