import time
import random
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _get_job(job_id):
    #Avoid circular importing
    from .models import Job
    return Job.objects.get(id = job_id)


@shared_task(bind=True, max_tries = 3, default_retry_delay = 60)
def process_job(self, job_id):
    """Core task - handles the full lifecycle of a job. bind = True gives access tp self(the task instance)"""
    from .models import Job

    try:
        job = _get_job(job_id)
    except Job.DoesNotExist:
        logger.error(f"Job {job_id} not found, Skipping.")
        return

    job.mark_processing(celery_task_id=self.request.id)
    logger.info(f"Processing job {job_id} | type={job.job_type} | attempt={self.request.retries +1}")

    try:
        result = _execute_job(job.job_type, job.payload)
        job.mark_completed(result=result)
        logger.info(f"Job {job_id} completed in {job.processing_time_seconds:.2f}s")
    except Exception as exc:
        job.retry_count+=1
        job.save(update_fields=['retry_count'])

        logger.warning(f"Job {job_id} failed on attempt {job.retry_count}: {exc}")
        if job.retry_count < job.max_tries:
            #Exponential backoff: 60s, 120s, 240s
            countdown = 60 * (2**(job.retry_count-1))
            logger.info(f"Retrying job {job_id} in {countdown}s")
            raise self.retry(exc= exc, countdown=countdown)
        else:
            job.mark_failed(error=str(exc))
            logger.error(f"Job {job_id} exhaust all retries. Marked as failed")


def _execute_job(job_type, payload):
    """Simulates actual work based on job type. In a real system, replce each block with actual logic."""
    if job_type =='email_send':
        time.sleep(random.uniform(0.5, 1.5)) #Simulate SMTP call
        return {
            'status' : 'sent',
            'recipient' : payload.get('to', 'unknown@example.com'),
            'message_id': f"msg_{random.randint(10000, 99999)}"
        }
    elif job_type == 'report_gen':
        time.sleep(random.uniform(2.0, 4.0)) #simulate heavy report build
        return {
            'report_url' : f"/reports/{random.randint(1000, 9999)}.pdf",
            'rows_processed' : random.randint(500, 5000)
        }
    
    elif job_type == 'data_process':
        time.sleep(random.uniform(1.0, 3.0)) #simulate DB-heavy operation
        #simulate occasionall failure for retry testing
        if random.random() < 0.2: # 20% failure rate
            raise Exception("Simulated data processing error")
        return {
            'record_processed' : random.randint(100, 1000),
            'errors' : 0
        }
    
    elif job_type == 'image_resize':
        time.sleep(random.uniform(0.3, 1.0)) #simulate image processing
        return {
            'original_size': payload.get('size', '1920x1080'),
            'resized_to': '800x600',
            'saved_path': f"/media/resized/{random.randint(1, 9999)}.jpg"
        }
    
    else:
        return ValueError(f"Unknown job type: {job_type}")