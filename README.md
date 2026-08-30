# JobQueue

A distributed async background job processing system built to explore and stress-test real-world backend engineering patterns — connection pooling, worker saturation, retry logic, and observability under load.

---

## Stack

| Layer | Technology |
|---|---|
| API | Django 4.2 + Django REST Framework |
| Task Queue | Celery 5.3.6 |
| Broker | Redis (Linux) / Memurai (Windows) |
| Result Backend | PostgreSQL (via django-celery-results) |
| Connection Pooler | PgBouncer (transaction mode) |
| Load Testing | Locust 2.24.1 |
| Monitoring | Prometheus + Grafana + custom pg_stat_activity logger |
| CI/CD | GitHub Actions |

**Hardware (all runs):** Lenovo E41-25, 2-core CPU  
**OS (primary):** Ubuntu 20.04, Python 3.8.10  
**OS (comparison):** Windows 11, PostgreSQL 18.3

---

## Features

- Full job lifecycle: `pending → processing → completed / failed`
- 3-tier priority queue routing: `jobs.high`, `jobs.default`, `jobs.low`
- Auto-retry with exponential backoff (60s → 120s → 240s)
- Dead letter queue with manual replay endpoint
- Celery Beat scheduled tasks (cleanup, queue stats)
- Health check endpoint (`/health/`) covering DB + Redis
- Per-job timing metrics: `queue_delay_seconds`, `processing_time_seconds`, `total_time_seconds`
- Prometheus + Grafana integration
- Custom connection and queue depth loggers (CSV, timestamped)

---

## Performance Results

### Baseline — Before Fix (Linux, prefork, no PgBouncer)

**Hardware:** Lenovo E41-25, Linux, 2-core CPU  
**Stack:** Django runserver · 1 Celery worker (prefork, concurrency=4) · PostgreSQL default config (max_connections=100)

| Users | RPS | Avg (ms) | p95 (ms) | p99 (ms) | Error Rate | Status |
|---|---|---|---|---|---|---|
| 50 | 21.2 | 214 | 470 | 5,500* | 0% | Stable |
| 100 | 31.0 | 984 | 2,100 | 2,700 | 0% | Approaching limit |
| 150 | 6.4 | 18,848 | 70,000 | 98,000 | 21.4% | **Ceiling breached** |
| 200 | 7.9 | 15,880 | 52,000 | 86,000 | 23.6% | **Ceiling breached** |

*p99 at 50 users inflated by worker restart during test — anomaly, not system limit.

**Breaking point: ~150 concurrent users**

Error signature:
```
FATAL: sorry, too many clients already
django.db.utils.OperationalError: connection to server failed
```

---

### Fix Series — 5 Controlled Experiments

A structured RCA was conducted across 5 runs to identify and resolve the root cause.  
All runs: 150 concurrent users · 20/s spawn rate · 2-minute duration.

| Run | Environment | Celery Pool | CONN_MAX_AGE | PgBouncer | Requests | Failures | RPS | Peak Connections |
|---|---|---|---|---|---|---|---|---|
| A — Before fix | Linux | prefork | 0 | No | 757 | 162 (21.4%) | 6.4 | 104 / 100 ⚠ |
| B — CONN_MAX_AGE | Linux | prefork | 60 | No | 1,085 | 161 (14.8%) | 9.1 | 104 / 100 ⚠ |
| C — PgBouncer | Linux | prefork | 0 | Yes | 4,078 | 1 (0.0%) | 34.2 | 26 / 100 ✓ |
| D — Windows | Windows | threads | 0 | No | 1,780 | 0 (0.0%) | 14.7 | 11 / 100 ✓ |
| E — Linux threads | Linux | threads | 0 | No | 1,628 | 126 (7.7%) | 13.7 | 100 / 100 ⚠ |

---

### Root Cause

**Primary bottleneck — PostgreSQL connection exhaustion:**

Django opens one DB connection per web thread. With `CONN_MAX_AGE=0` (default), connections close after each request — but the thread stays alive, holding the connection idle while waiting for the next request. At 150 concurrent users, 100 threads are simultaneously in-flight, each holding one idle connection. The 101st is rejected by PostgreSQL.

Evidence from `pg_stat_activity` during peak load:
```
active connections: 1–4
idle connections:   96–99
```
Connections were not being used — they were being held. This is Django's thread-local connection behaviour, not Celery's.

**Why CONN_MAX_AGE=60 improved but did not fix it (Run B):**  
`CONN_MAX_AGE` reduces how often connections open and close — reducing churn and improving throughput (+43% RPS). But it does not cap how many connections can be open simultaneously. Peak connection count was identical: 104 in both Run A and Run B.

**Why PgBouncer resolved it (Run C):**  
PgBouncer in transaction pool mode enforces a hard cap at the pooler layer. Django and Celery connect to PgBouncer (port 6432), which passes only `default_pool_size=20` real backend connections to PostgreSQL. The ceiling is enforced before PostgreSQL sees the traffic. Zero FATAL errors reached PostgreSQL during the run.

**Why Celery pool type is not the cause (Run E):**  
Run D (Windows, threads pool) did not reproduce exhaustion. Initial hypothesis: prefork creates separate OS processes each holding a connection, while threads share one process. Run E tested this directly — same Linux machine, same PostgreSQL 12, threads pool only. Exhaustion reproduced at the same threshold. Pool type is not the primary cause. The Django web-layer connection-holding behaviour is identical regardless of Celery pool type.

**Why Windows behaved differently (Run D):**  
Observed difference — connections peaked at 11 on Windows vs 100 on Linux under identical load. The most likely explanation is differences in how Windows' threading model interacts with Django's per-thread connection lifecycle. Not fully isolated — but pool type is ruled out as the explanation.

---

### Secondary Bottleneck — Celery Worker Saturation

At 100 users, 801 jobs were pending at test end. Job creation rate (~9/s) exceeded worker processing rate (~2/s). `queue_delay_seconds` was already elevated at 0% error rate — the API layer looked healthy while the processing layer was saturating.

Fix: horizontal worker scaling (multiple worker instances or higher concurrency).

---

### Key Takeaways

**1. CONN_MAX_AGE ≠ connection cap.**  
Reduces churn. Does not limit concurrent connection count. Peak connections unchanged before and after.

**2. PgBouncer is the structural fix.**  
Enforces a hard cap at the pooler layer. Required config: `CONN_MAX_AGE=0` in Django (let PgBouncer own the pooling), `pool_mode=transaction` in PgBouncer.

**3. Celery pool type does not determine whether exhaustion occurs on Linux.**  
Django web threads hold idle connections — not Celery workers. Proved by Run E.

**4. Surface metrics can be healthy while the system is saturated.**  
0% error rate at 100 users. But `queue_delay_seconds` showed 801 pending jobs. Monitor percentile latency and queue depth — not just error rate. This mirrors a pattern seen in production: API metrics look fine until a downstream resource quietly saturates.

**5. Environment is a variable.**  
Same code, same load, different OS — different result. Always test in the target environment.

---

## Observability

Two custom monitoring scripts run in parallel during load tests:

**`log_connections.py`** — polls `pg_stat_activity` every second, writes to CSV:
- `total_connections`, `pct_of_max`, `active`, `idle`, `waiting`, `idle_in_transaction`
- `longest_active_query_secs`, `longest_transaction_secs`
- `locks_waiting`, `deadlocks_cumulative`, `temp_files_cumulative`

**`log_queue_depth.py`** — polls Redis queue depths every second, writes to CSV:
- `jobs.high`, `jobs.default`, `jobs.low`, `total_queued`

Both use the same timestamp format (`%Y-%m-%d %H:%M:%S`) for cross-correlation.

> **Timezone note:** Locust HTML/CSV reports use UTC. All other logs use IST (UTC+5:30). Add 5:30 when correlating Locust timestamps with system logs.

---

## PgBouncer Config (Fix)

```ini
[databases]
jobqueue_db = host=127.0.0.1 port=5432 dbname=jobqueue_db

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
pool_mode = transaction
max_client_conn = 200
default_pool_size = 20
```

Django `settings.py` with PgBouncer:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'jobqueue_db',
        'HOST': 'localhost',
        'PORT': '6432',       # PgBouncer, not 5432
        'CONN_MAX_AGE': 0,    # Let PgBouncer own pooling
    }
}
```

---

## Project Structure

```
JobQueue/
├── config/
│   ├── settings.py
│   ├── celery.py
│   └── urls.py
├── jobs/
│   ├── models.py         # Job, DeadLetterJob
│   ├── views.py          # JobListCreate, JobDetail, JobStats, DLQ replay, HealthCheck
│   ├── tasks.py          # process_job, cleanup_old_jobs, log_queue_stats
│   └── serializers.py
├── PerformanceTesting/
│   ├── locustfile.py
│   ├── log_connections.py
│   ├── log_queue_depth.py
│   └── celery_thread_test/
│       └── [Run E artifacts]
└── .github/
    └── workflows/
        └── ci.yml
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/jobs/` | List all jobs (filter by status, job_type) |
| POST | `/jobs/` | Create and enqueue a job |
| GET | `/jobs/<id>/` | Poll job status and result |
| GET | `/jobs/stats/` | Aggregated counts by status |
| POST | `/jobs/dlq/<id>/replay/` | Replay a dead letter job |
| GET | `/health/` | System health (DB + Redis + queue depth) |

---

## Local Setup

```bash
git clone https://github.com/Github-Sathish/JobQueue.git
cd JobQueue
python -m venv jobvenv && source jobvenv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add DB credentials
python manage.py migrate
python manage.py runserver

# In a separate terminal
celery -A config worker -Q jobs.high,jobs.default,jobs.low --loglevel=info

# Run load test
locust -f PerformanceTesting/locustfile.py --host=http://localhost:8000
```

**Windows (Celery pool workaround — prefork not supported):**
```bash
celery -A config worker -P threads --concurrency=4 -Q jobs.high,jobs.default,jobs.low --loglevel=INFO
```