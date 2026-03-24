# JobQueue
A distributed task queue system for processing background jobs


## Performance results

Hardware: Lenovo E41-25, Linux, 2-core CPU
Stack: Django dev server, 1 Celery worker (concurrency=4), PostgreSQL default config

| Users | RPS  | Avg (ms) | p95 (ms) | p99 (ms) | Error rate | Breaking point |
|-------|------|----------|----------|----------|------------|----------------|
| 50    | 21.2 | 214      | 470      | 5,500*   | 0%         | No             |
| 100   | 31.0 | 984      | 2,100    | 2,700    | 0%         | Approaching    |
| 200   | 7.9  | 15,880   | 52,000   | 86,000   | 23.6%      | YES            |

*p99 at 50 users inflated by worker restart during test — test anomaly, not system limit.

### Breaking point: ~150 concurrent users

System breaks between 100 and 200 users.

Root cause: PostgreSQL default max_connections=100 exhausted.
Django opens a new DB connection per thread per request without CONN_MAX_AGE.
At 200 concurrent users: connection pool saturated → 500 errors → cascading failures.

Error signature:
  FATAL: sorry, too many clients already
  django.db.utils.OperationalError: connection to server failed

### Bottleneck analysis

Primary bottleneck — PostgreSQL connection pool exhaustion:
  Django's default behaviour opens a new DB connection for every request.
  PostgreSQL default max_connections = 100.
  At 200 users, simultaneous connections exceed this limit.
  Fix: CONN_MAX_AGE=60 in DATABASES setting (persistent connections).
  Production fix: PgBouncer connection pooler in front of PostgreSQL.

Secondary bottleneck — single Celery worker saturation:
  At 100 users, 801 jobs were pending at test end (worker falling behind).
  Job creation rate (~9/sec) exceeded worker processing rate (~2/sec).
  Queue delay: 160s at 50 users, 13s at 100 users (different test conditions).
  Fix: horizontal worker scaling (multiple worker processes or higher concurrency).

Redis: not a bottleneck at any tested load level.
  All broker operations completed without measurable latency.

### What I learned

The surface metrics looked acceptable at 100 users (0% errors, 31 RPS).
But measuring queue_delay_seconds revealed 801 jobs still pending at test end —
the worker was already saturated. The API layer was healthy; the processing
layer was not. At 200 users, the DB connection pool exhausted and caused
cascading 500 errors across all endpoints including health checks.

This mirrors a pattern I've seen in production — API latency metrics look
fine until a downstream resource (DB connections, worker capacity) quietly
saturates. The fix is not more API instances — it's identifying and addressing
the actual constraint.