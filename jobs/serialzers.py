from rest_framework import serializers
from .models import Job


class JobCreateSerializer(serializers.ModelSerializer):
    
    class Meta:
        model = Job
        fields = ['job_type', 'payload']
    
    def validate_job_type(self, value):
        valid_types = [choice[0] for choice in Job.JobType.choices]
        if value not in valid_types:
            raise serializers.ValidationError(f"Invalid job type. Choose from: {valid_types}")
        return value



class JobDetailSerializer(serializers.ModelSerializer):


    class Meta:
        model = Job
        fields = ['id', 'job_type', 'status', 'payload', 'result', 'error', 'retry_count', 'max_tries', 'created_at', 'started_at', 'completed_at', 'celery_task_id', 'queue_delay_seconds', 'processing_time_seconds', 'total_time_seconds']


class JobListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = ['id', 'job_type', 'status', 'retry_count', 'created_at']