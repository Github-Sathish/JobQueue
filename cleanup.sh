#!/bin/bash

echo "========================================="
echo "BEFORE CLEANUP"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "========================================="

echo
echo "Database:"
python manage.py shell -c "
from jobs.models import Job
from django.db.models import Count, Q
print(Job.objects.aggregate(
    pending=Count('id', filter=Q(status='pending')),
    processing=Count('id', filter=Q(status='processing')),
    failed=Count('id', filter=Q(status='failed'))
))
"

echo
echo "Redis keys:"
redis-cli DBSIZE

echo
echo "Flushing Redis..."
redis-cli FLUSHDB

echo
echo "Deleting Jobs..."
python manage.py shell -c "
from jobs.models import Job
count = Job.objects.count()
Job.objects.all().delete()
print(f'Deleted {count} jobs')
"

echo
echo "========================================="
echo "AFTER CLEANUP"
echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "========================================="

echo
echo "Database:"
python manage.py shell -c "
from jobs.models import Job
from django.db.models import Count, Q
print(Job.objects.aggregate(
    pending=Count('id', filter=Q(status='pending')),
    processing=Count('id', filter=Q(status='processing')),
    failed=Count('id', filter=Q(status='failed'))
))
"

echo
echo "Redis keys:"
redis-cli DBSIZE