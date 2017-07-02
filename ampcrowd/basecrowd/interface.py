import json
import uuid

from django.core.urlresolvers import reverse

from models import CrowdModelSpecification


# Required implementation for a new crowd type
class CrowdInterface(object):
    def __init__(self, crowd_name):
        self.crowd_name = crowd_name

    @staticmethod
    def validate_configuration(configuration):
        """ Validate crowd-specific configuration options.

        `configuration` is a dictionary of crowd-specific options as specified
        by the API. This method should verify that all required options are
        included and valid, and return `True` if so and `False` otherwise
        """
        # Dummy implementation, do no validation.
        return True

    @staticmethod
    def create_task(configuration, content):
        """ Do the necessary work to create a task on the crowd platform.

        For example, create a HIT on AMT using the APIs. `configuration` is a
        dictionary containing settings passed via the public API. `content`
        is the actual content for this crowd task. This function must return a
        unique identifier for the new task.
        """
        # Dummy implementation, return a random string
        return uuid.uuid4()

    @staticmethod
    def task_pre_save(task_object):
        """ Process newly created task objects before they are saved to the DB.

        `task_object` will be an UNSAVED object with the `task_model` class
        according to this crowd's model specification. Its task_id field will be
        set to the id returned by the `create_task` method. This method can
        modify the unsaved object (e.g., set custom fields) before it is saved
        to the database. This method SHOULD NOT save the object--it will be
        saved later.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def group_pre_save(group_object):
        """ Process new task group objects before they are saved to the DB.

        `group_object` will be an UNSAVED object with the `group_model` class
        according to this crowd's model specification. Its group_id field will
        be set to the id passed in via the external API. This method can
        modify the unsaved object (e.g., set custom fields) before it is saved
        to the database. This method SHOULD NOT save the object--it will be
        saved later.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def pay_worker_bonus(worker_object, task_object, bonus_amount, reason):
        """ Pay an additional bonus to a worker.

        `worker_object` is an instance of this crowd's worker model,
        `task_object` is an instance of this crowd's task model, `bonus_amount`
        is a float containing the amount of money to pay the worker in USD, and
        `reason` is a string explaining why the bonus has been granted. This
        method should attempt to pay the appropriate amount to the worker on the
        remote platform, or do nothing if bonus payments aren't supported.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def reject_task(task_object, worker_object, reason):
        """ Reject work done by a worker instead of paying them.

        `task_object` is an instance of this crowd's task model, `worker_object`
        is an instance of this crowd's worker model, and `reason` is a string
        explaining why the work has been rejected. This method should reject the
        assignment of this worker to this task on the remote crowd platform, or
        do nothing if task rejection isn't supported.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def expire_tasks(task_objects):
        """ Expire multiple tasks on the crowd platform.

        Expiration means making the task no longer available for new workers to
        accept. It does not necessarily imply deletion, though some crowd
        platforms may choose to implement it that way.

        `task_objects` is an iterable containing multiple instances of this
        crowd's task_model. This method should not modify the objects
        themselves, just handle the remote cleanup.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def delete_tasks(task_objects):
        """ Delete multiple tasks on the crowd platform.

        `task_objects` is a queryset containing multiple instances of this
        crowd's task_model. This method should not delete the objects
        themselves, just handle the remote cleanup.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def get_assignment_context(request):
        """ Extract crowd context from a request for the interface.

        `request` is a Django HttpRequest object created when the crowd platform
        requests an assignment interface from this server. This method should
        return a dictionary containing custom context needed to render templates
        as well as the following fields:

        * `task_id`: the task being requested.
        * `is_accepted`: has the worker committed to working on the task, or is
                         it just a preview?
        * `worker_id`: the worker working on the task (optional if `is_accepted`
                       is False).

        Additionally, the keys 'content', 'group_context', and 'response_url'
        are reserved.
        """
        # Base implementation, look for the fields in the request dictionary.
        request_data = request.GET if request.method == 'GET' else request.POST
        return {'task_id': request_data.get('task_id', None),
                'worker_id': request_data.get('worker_id', None),
                'is_accepted': request_data.get('is_accepted', True)}

    @staticmethod
    def get_response_context(request):
        """ Extract response data from a request.

        `request` is a Django HttpRequest object created when the crowd
        interface posts data from an assignment. This method should return a
        dictionary containing custom context needed to store models as well as
        the following fields:

        * `task_id`: the task being requested.
        * `worker_id`: the worker working on the task.
        * `assignment_id`: a unique id for the assignment of worker to task.
        * `answers`: the assignment responses in json form (task-type dependent)

        """
        # Base implementation, look for the fields in the request dictionary
        request_data = request.GET if request.method == 'GET' else request.POST
        return {
            'task_id': request_data.get('task_id', None),
            'worker_id': request_data.get('worker_id', None),
            'assignment_id': request_data.get('assignment_id', None),
            'answers': request_data.get('answers', None)
        }

    @staticmethod
    def worker_pre_save(worker_object):
        """ Process new worker objects before they are saved to the DB.

        `worker_object` will be an UNSAVED object of the `worker_model` class
        according to this crowd's model specification. Its worker_id field will
        be set according to the context provided by `get_assignment_context`.
        This method can modify the unsaved object (e.g., set custom fields)
        before it is saved to the database. This method SHOULD NOT save the
        object--it will be saved later.
        """
        # Dummy implementation, do nothing
        pass

    @staticmethod
    def response_pre_save(assignment_object):
        """ Process new responses before they are saved to the DB.

        `assignment_object` will be an UNSAVED object of the `assignment_model`
        class according to this crowd's model specification. Its worker,
        task, content, and assignment_id fields will be set according to the
        context provided by `get_response_context`. This method can modify the
        unsaved object (e.g., set custom fields) before it is saved to the
        database. This method SHOULD NOT save the object--it will be saved
        later.
        """
        # Dummy implementation, do nothing
        pass

    def get_frontend_submit_url(self, crowd_config):
        """ Returns a url path to redirect to after a worker submits a task."""
        # Dummy implementation, just refresh the page on submit.
        return ''

    def get_assignment_url(self):
        """ Return a url path to the view which will produce the task interface.

        Subclasses shouldn't need to override this.
        """
        return reverse('basecrowd:get_assignment', args=[self.crowd_name])

    def get_backend_submit_url(self):
        """ Return a url path to the view which will handle crowd responses.

        Subclasses shouldn't need to override this.
        """
        return reverse('basecrowd:post_response', args=[self.crowd_name])

    ###############################################################################
    # Internal methods that don't need to be overwritten or called by subclasses. #
    ###############################################################################

    # Validate context dictionaries
    @staticmethod
    def require_context(context_dictionary, required_keys, exc):
        if any([context_dictionary.get(k) is None for k in required_keys]):
            raise exc

    # Validate the API create request.
    def validate_create_request(self, request_json):
        try:
            json_dict = json.loads(request_json)
        except ValueError:  # data was invalid JSON
            return False

        try:
            # require top-level fields
            self.require_context(
                json_dict,
                ['configuration', 'group_id', 'group_context', 'content'],
                ValueError())

            # require configuration options
            configuration = json_dict['configuration']
            self.require_context(
                configuration,
                ['task_type', 'task_batch_size', 'num_assignments',
                 'callback_url'],
                ValueError())

            # require retainer pool sub-options, if present
            if 'retainer_pool' in configuration:
                retainer_config = configuration['retainer_pool']
                if retainer_config.get('create_pool', True):
                    self.require_context(
                        retainer_config,
                        ['pool_size',
                         'min_tasks_per_worker',
                         'waiting_rate',
                         'task_rate',
                         'list_rate'],
                        ValueError())
                else:
                    self.require_context(
                        retainer_config,
                        ['pool_id'],
                        ValueError())

        except ValueError:
            return False

        # Require at least one record for crowd processing.
        content = json_dict['content']
        point_identifiers = content.keys()
        if len(point_identifiers) == 0:
            return False

        # Do crowd-specific validation
        crowd_config = configuration.get(self.crowd_name, {})
        return self.validate_configuration(crowd_config)


class CrowdRegistry(object):
    registered_crowds = {}

    # Register a new crowd with an interface.
    @classmethod
    def register_crowd(cls, interface, **model_classes):
        name = interface.crowd_name
        if name in cls.registered_crowds:
            raise ValueError("Crowd already registered: " + name)
        model_spec = CrowdModelSpecification(name, **model_classes)
        model_spec.add_model_rels()
        cls.registered_crowds[name] = (interface, model_spec)

    # Look up the model specification for a crowd.
    @classmethod
    def get_registry_entry(cls, crowd_name):
        interface, model_spec = cls.registered_crowds.get(crowd_name,
                                                          (None, None))
        if not interface and not model_spec:
            raise ValueError("Invalid crowd name: " + crowd_name)
        return interface, model_spec

    # Get the entire registry
    @classmethod
    def get_registry(cls):
        return cls.registered_crowds
