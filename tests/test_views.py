from django.urls import reverse
from rest_framework.test import APIRequestFactory
from mixer.backend.django import mixer
from jobs.views import JobDetailView, JobStatsView
from rest_framework import status
import pytest


@pytest.fixture
def factory():
    return APIRequestFactory()


def test_job_detail(factory, db): #factory and db or fixtures - db enables the ""FAILED tests/test_views.py::test_job_stats_view - RuntimeError: Database access not allowed, use the "django_db" mark, or the "db" or "transactional_db" fixtures to enable it.""
    job = mixer.blend('jobs.Job')
    path = reverse('job-detail',
                    kwargs={'job_id': job.id})
    req = factory.get(path)
    response = JobDetailView.as_view()(
        req,
        job_id = job.id
    )

    assert response.status_code == status.HTTP_200_OK


def test_job_stats_view(factory, db):
    # job = mixer.blend('jobs.Job')
    path = reverse('job-stats')
    req = factory.get(path)
    resp = JobStatsView.as_view()(req)

    assert resp.status_code == 200 







# @pytest.mark.django_db
# class TestView:

#     @classmethod
#     def setup_class(cls):
#         cls.factory = APIRequestFactory()

#     def test_job_detail(self):
#         job = mixer.blend('jobs.Job')
#         path = reverse('job-detail',
#                        kwargs={'job_id': job.id})
#         # req = APIRequestFactory().get(path)
#         req = self.factory.get(path)
#         response = JobDetailView.as_view()(
#             req,
#             job_id = job.id
#         )

#         assert response.status_code == status.HTTP_200_OK

    
#     def test_job_stats_view(self):
#         # job = mixer.blend('jobs.Job')
#         path = reverse('job-stats')
#         # req = APIRequestFactory().get(path)
#         req = self.factory.get(path)
#         resp = JobStatsView.as_view()(req)

#         assert resp.status_code == 200 