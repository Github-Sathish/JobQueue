import logging
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Avg, Count, Q
from django.utils import timezone

from .models import Job
from .serialzers import JobCreateSerializer, JobDetailSerializer, JobListSerializer
from .tasks import process_job

logger = logging.getLogger(__name__)






class JobListCreateView(APIView):
    def get(self, request):
        """get /jobs/ - list all jobs with optional status filter"""
        jobs = Job.objects.all()

        status_filter = request.query_params.get('status')
        if status_filter:
            jobs = jobs.filter(status=status_filter)
        
        job_type_filter = request.query_params.get('job_type')
        if job_type_filter:
            jobs = jobs.filter(job_type= job_type_filter)

        serializer = JobListSerializer(jobs[:50], many = True) #cap at 50 for now
        return Response(serializer.data)
    
    def post(self, request):
        """POST /jobs/ - Create job and enqueue it immediately"""
        serializer = JobCreateSerializer(data = request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        job = serializer.save() #both do the same thing
        # job = Job.objects.create(job_type=serializer.validated_data['job_type'], payload=serializer.validated_data['payload'])

        queue_map = {
            'high': 'jobs.high',
            'default' : 'jobs.default',
            'low' : 'jobs.low',
        }

        queue = queue_map.get(job.priority, 'jobs.default')

        # process_job.delay(str(job.id))
        process_job.apply_async(args=[str(job.id)], queue= queue)

        logger.info(f"Job {job.id} created and enqueued | type={job.job_type}")

        return Response(JobDetailSerializer(job).data, status=status.HTTP_201_CREATED)
    

class JobDetailView(APIView):
    def get(self, request, job_id):
        """Get /jobs/{id}/ - poll for job status and result"""
        job = get_object_or_404(Job, id=job_id)
        serializer = JobDetailSerializer(job)
        return Response(serializer.data)
    

class JobStatsView(APIView):
    def get(self, request):
        """Get /jobs/stats/ - aggregates stats for monitring dashboard"""
        stats = Job.objects.aggregate(
            total=Count('id'),
            pending=Count('id', filter=Q(status='pending')),
            processing=Count('id', filter=Q(status='processing')),
            completed=Count('id', filter=Q(status='completed')),
            failed=Count('id', filter=Q(status='failed')),
        )
        return Response(stats)


class DeadLetterReplayView(APIView):
    """POST /jobs/dlq/{job_id}/replay/
    Manually requeue a permanently failed job."""
    def post(self, request, job_id):
        from .models import DeadLetterJob
        dlq_entry = get_object_or_404(DeadLetterJob, original_id__id = job_id)

        if dlq_entry.replayed:
            return Response({"error":"job already replayed"}, status=status.HTTP_400_BAD_REQUEST)

        #Reset the job detials to new
        job = dlq_entry.original_job
        job.status = Job.Status.PENDING
        job.retry_count = 0
        job.started_at = None
        job.completed_at = None
        job.error = None
        job.save()

        dlq_entry.replayed = True
        dlq_entry.replayed_at = timezone.now()
        dlq_entry.save()

        #Re-enqueue
        process_job.apply_async(args=[str(job.id)], queue = 'jobs.high') #replay get priority

        return Response({"message": "Re-enqueued successfully",}, status=status.HTTP_200_OK)


class HealthCheckView(APIView):
    """
    GET /health/
    Returns status of all system dependencies.
    Used by load balancers and monitoring tools to decide
    whether to send traffic to this instance.
    """
    def get(self, request):
        import django.db
        health = {
            'status': 'healthy',
            'checks':{}
        }

        #check postgres
        try:
            django.db.connection.ensure_connection()
            health['checks']['database'] = 'ok'
        except Exception as exc:
            health['checks']['database'] = f"error: {str(exc)}"
            health['status'] = 'unhealthy'
        
        #check redis
        try:
            from django.core.cache import cache
            cache.set('health_check', 'ok', 10)
            assert cache.get('health_check') == 'ok'
            health['checks']['redis'] = 'ok'
        except Exception as exc:
            health['checks']['redis'] = f"error: {str(exc)}"
            health['status'] = 'unhealthy'

        # Queue depth snapshot
        from .models import Job
        health['queue_depth'] = Job.objects.filter(
            status='pending'
        ).count()

        status_code = 200 if health['status'] == 'healthy' else 503
        return Response(health, status=status_code)