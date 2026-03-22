from django.db import models
import uuid
from django.utils import timezone


class Job(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
    # Tuple of (database_value, human_readable_name)
    # JOBTYPE_CHOICES = [
    #     ('email_send', 'Email Send'),
    #     ('report_gen', 'Report Generation'),
    #     ('data_process',  'Data Processing'),
    #     ('image_resize',  'Image Resize'),
    # ]

    class JobType(models.TextChoices):
        EMAIL_SEND    = 'email_send',    'Email Send'
        REPORT_GEN    = 'report_gen',    'Report Generation'
        DATA_PROCESS  = 'data_process',  'Data Processing'
        IMAGE_RESIZE  = 'image_resize',  'Image Resize'

    class Priority(models.TextChoices):
        HIGH = 'high', 'High'
        Default = 'default', 'Default'
        LOW = 'low', 'LOW' 

    # identity
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=50, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    # payload & result
    payload = models.JSONField(default=dict)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)

    # retry tracking
    retry_count = models.PositiveSmallIntegerField(default=0)
    max_tries = models.PositiveSmallIntegerField(default=3)

    # Timing - this is useful for performance calculations
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    # Celery task reference
    celery_task_id = models.CharField(max_length=255, null=True, blank=True)

    priority = models.CharField(max_length=10, choices=Priority.choices, default = Priority.Default)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),           # fast filtering by status
            models.Index(fields=['job_type']),
            models.Index(fields=['created_at']),
            models.Index(fields=['status', 'created_at']),  # composite for dashboard queries
        ]

    def __str__(self):
        return f"{self.job_type} [{self.status}] - {self.id}"
    
    # computing timing properties
    @property
    def queue_delay_seconds(self):
        # time from job creation to worker picking it up (queue wait time)
        if self.started_at and self.created_at:
            return (self.started_at - self.created_at).total_seconds()
        return None
    
    @property
    def processing_time_seconds(self):
        #time from worker start to complete
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @property
    def total_time_seconds(self):
            if self.created_at and self.completed_at:
                return (self.completed_at - self.created_at).seconds
            return None
    

    # state transition methods
    def mark_processing(self, celery_task_id=None):
        self.status = self.Status.PROCESSING
        self.started_at = timezone.now()
        if celery_task_id:
            self.celery_task_id = celery_task_id
        self.save(update_fields=['status', 'started_at', 'celery_task_id'])
    
    def mark_completed(self, result=None):
        self.status = self.Status.COMPLETED
        self.completed_at = timezone.now()
        if result:
            self.result = result
        self.save(update_fields=['status', 'completed_at', 'result'])
    
    def mark_failed(self, error=None):
        self.status = self.Status.FAILED
        self.completed_at = timezone.now()
        if error:
            self.error = error
        self.save(update_fields=['status', 'completed_at', 'error'])
    

class DeadLetterJob(models.Model):
    """jobs that exhausted all retries land here."""
    original_job = models.OneToOneField(Job, on_delete = models.CASCADE, related_name = 'deal_letter')
    failure_reason = models.TextField()
    failed_at = models.DateTimeField(auto_now_add = True)
    retry_count = models.PositiveSmallIntegerField()
    last_error = models.TextField()

    #for future replay support
    replayed = models.BooleanField(default = False)
    replayed_at = models.DateTimeField(null = True, blank = True)

    class Meta:
        ordering = ["-failed_at"]

    def __str__(self) -> str:
        return f"DLQ: {self.original_job.job_type} [{self.original_job.id}]"