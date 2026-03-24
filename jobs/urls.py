from django.urls import path
from . import views


urlpatterns = [
    path('', views.JobListCreateView.as_view(), name='job-list-create'),
    path('stats/', views.JobStatsView.as_view(), name = 'job-stats'),
    path('dlq/<uuid:job_id>/replay/', views.DeadLetterReplayView.as_view(), name='dlq-replay'),
    path('<uuid:job_id>/', views.JobDetailView.as_view(), name = 'job-detial'),
]