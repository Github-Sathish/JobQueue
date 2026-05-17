from django.urls import reverse, resolve
import pytest
from jobs.views import JobDetailView

#keep one assert per fun
class TestUrls:
    def test_detail_url1(self):
        path = reverse('job-detail', kwargs={'job_id':'cebcf3fb-82b5-4a01-852a-5ce9a121480c'})
        assert resolve(path).func.view_class == JobDetailView

    def test_detail_url2(self):
        path = reverse('job-detail', kwargs={'job_id':'550e8400-e29b-41d4-a716-446655440000'})
        assert resolve(path).func.view_class == JobDetailView