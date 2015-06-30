from django.conf.urls import patterns, url

from basecrowd import views

urlpatterns = patterns(
    '',
    url(r'^(\w+)/assignments/$', views.get_assignment, name='get_assignment'),
    url(r'^(\w+)/assignments/retainer/$', views.assign_retainer_task,
        name='assign_retainer_task'),
    url(r'^(?P<crowd_name>\w+)/assignments/worker/(?P<worker_id>[^/]+)/task/(?P<task_id>[^/]+)$',
        views.get_retainer_assignment, name='get_retainer_assignment'),
    url(r'^(?P<crowd_name>\w+)/retainer/finish$', views.finish_pool, name='finish_pool'),
    url(r'^(?P<crowd_name>\w+)/workers/(?P<worker_id>[^/]+)/understands_retainer$',
        views.understands_retainer, name='understands_retainer'),
    url(r'^(\w+)/responses/$', views.post_response, name='post_response'),
    url(r'^(\w+)/tasks/$', views.create_task_group, name='create_tasks'),
    url(r'^(\w+)/purge_tasks/$', views.purge_tasks, name='purge_tasks'),
    url(r'^(?P<crowd_name>\w+)/retainer/ping/$', views.ping, name='ping'),
)
