from mixer.backend.django import mixer
from datetime import timedelta
from django.utils import timezone
import pytest

@pytest.fixture
def job_timing_fixture(db):
    created_at = timezone.now()
    started_at = created_at + timedelta(seconds=30)
    completed_at = created_at + timedelta(seconds=90)

    return mixer.blend('jobs.Job', created_at=created_at,started_at=started_at, completed_at=completed_at)



@pytest.mark.parametrize('property_name, expected', [
    ('queue_delay_seconds', 30.0),
    ('processing_time_seconds', 60.0),
    ('total_time_seconds', 90.0),
])
def test_job_time_properties(job_timing_fixture, property_name, expected):
    actual = getattr(job_timing_fixture, property_name)
    assert actual == pytest.approx(expected, rel=2e-2)



# @pytest.mark.django_db
# class TestModels:
#     def test_total_time_seconds(self):
#         created_at = timezone.now()
#         completed_at = created_at + timedelta(seconds=120)
#         total_time = mixer.blend('jobs.Job', created_at=created_at, completed_at=completed_at)

#         assert total_time.total_time_seconds == pytest.approx(120, rel=1e-2)