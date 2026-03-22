from django.contrib import admin
from .models import Job


@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display  = ['id', 'job_type', 'status', 'retry_count', 'created_at', 'queue_delay_seconds']
    list_filter   = ['status', 'job_type']
    search_fields = ['id', 'celery_task_id']
    readonly_fields = ['id', 'created_at', 'started_at', 'completed_at', 'celery_task_id',
                       'queue_delay_seconds', 'processing_time_seconds', 'total_time_seconds']