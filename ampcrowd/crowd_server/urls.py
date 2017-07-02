from django.conf.urls import patterns, include, url
from django.conf import settings
from django.conf.urls.static import static

#from django.contrib import admin
#admin.autodiscover()

urlpatterns = patterns(
    '',
    url(r'^crowds/', include('basecrowd.urls', namespace="basecrowd")),
    url(r'^crowds/internal/', include('internal.urls', namespace="internal")),
    url(r'^dashboard/', include('results_dashboard.urls',
                                namespace="dashboard")),
)

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
