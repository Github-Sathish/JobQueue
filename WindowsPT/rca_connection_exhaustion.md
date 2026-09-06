# RCA: PostgreSQL Connection Exhaustion Under Load — JobQueue

**Author:** Sathish
**Project:** Async Background Job Processing System (JobQueue)
**Scope:** 150 concurrent user load test — investigation limited to this scenario
**Date range:** 2026-08-01 to 2026-08-02

---

## 1. Problem Statement

During Locust load testing of the JobQueue API at 150 concurrent users, requests began failing with:

```
django.db.utils.OperationalError: connection to server at "localhost" (127.0.0.1),
port 5432 failed: FATAL:  sorry, too many clients already
```

At lower concurrency (50, 100 users), no errors occurred. The failure was specific to sustained load at 150 users.

---

## 2. Environment

| Component | Detail |
|---|---|
| OS | Ubuntu 20.04 |
| PostgreSQL | v12 |
| Python | 3.8.10 |
| Stack | Django + DRF, Celery, Redis, PostgreSQL |
| Celery worker | `--concurrency=4`, queues: `jobs.high`, `jobs.default`, `jobs.low` |
| Postgres config | `max_connections = 100` (default) |
| Postgres log path | `/var/log/postgresql/postgresql-12-main.log` |
| Load tool | Locust 2.24.1 |

---

## 3. Investigation

### 3.1 Reproducing and confirming

Ran Locust at 150 users, 20/s spawn rate, 2-minute duration, against the live stack (Django + Celery worker + Celery beat), while capturing:

- Postgres log (`tail -f` → file)
- Live connection count via `pg_stat_activity`, polled every 2s and logged to CSV with timestamps
- Celery worker terminal log
- Locust HTML/CSV report

### 3.2 What the logs showed

Postgres log confirmed a burst of `FATAL: sorry, too many clients already` errors, tightly clustered within roughly a 1-second window — consistent with a connection-count spike, not a slow leak.

The connection count CSV confirmed the mechanism directly:

| Run | Peak total connections | `max_connections` |
|---|---|---|
| Before fix | **104** | 100 |

The peak exceeded the ceiling — this is the direct cause of the `FATAL` errors.

### 3.3 Root cause

Two things compound at 150 concurrent users:

1. **Django's default per-request DB connection behavior** — without `CONN_MAX_AGE` set, Django opens a fresh DB connection for (roughly) every request and closes it at the end. Under high concurrency, many connections are open simultaneously rather than being reused.
2. **Celery workers hold their own separate connections** — each of the 4 worker processes maintains its own DB connection(s), independent of the Django web process's connections.

Together, Django's web-process connections + Celery's worker connections exceeded Postgres's `max_connections = 100` ceiling once load reached 150 concurrent users.

---

## 4. Fix Attempt 1 — `CONN_MAX_AGE`

### 4.1 Change

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'jobqueue_db',
        'USER': 'postgres',
        'PASSWORD': '',
        'HOST': 'localhost',
        'PORT': '',
        'CONN_MAX_AGE': 60,
    }
}
```

This tells Django to reuse a DB connection for up to 60 seconds instead of opening/closing one per request — reducing connection *churn*.

### 4.2 Result

Re-ran the identical 150-user, 2-minute test.

| Metric | Before Fix | + CONN_MAX_AGE | Change |
|---|---|---|---|
| Requests | 757 | 1,085 | +43% |
| Failures | 162 (21.4%) | 161 (14.8%) | rate ↓, count ~same |
| Avg latency | 18,848 ms | 11,631 ms | −38% |
| p95 | 70,000 ms | 37,000 ms | −47% |
| p99 | 98,000 ms | 49,000 ms | −50% |
| RPS | 6.4 | 9.1 | +42% |
| **Peak connections** | **104** | **104** | **no change** |

### 4.3 Interpretation

Throughput and latency improved substantially — connection reuse clearly reduced overhead per request. **But the peak connection count was identical (104) in both runs**, and failures persisted at a similar rate. This is the key finding: `CONN_MAX_AGE` reduces how *often* connections are opened, but does not cap how *many* can be open concurrently. At 150 users, Django + Celery can still simultaneously demand more connections than `max_connections` allows — `CONN_MAX_AGE` alone cannot fix a concurrency ceiling problem.

This pointed directly to needing a **connection pooler** to enforce a hard cap on real Postgres connections, independent of client-side (Django/Celery) demand.

---

## 5. Fix Attempt 2 — PgBouncer

### 5.1 Setup

Installed PgBouncer as a connection pooling layer between Django/Celery and PostgreSQL.

**`/etc/pgbouncer/pgbouncer.ini`:**
```ini
[databases]
jobqueue_db = host=127.0.0.1 port=5432 dbname=jobqueue_db

[pgbouncer]
listen_addr = 127.0.0.1
listen_port = 6432
auth_type = md5
auth_file = /etc/pgbouncer/userlist.txt
admin_users = postgres
pool_mode = transaction
max_client_conn = 200
default_pool_size = 20
```

`pool_mode = transaction` releases the real Postgres connection back to the pool after each transaction completes, rather than holding it for the life of the client connection — appropriate for Django's typical short-lived queries.

**Django `settings.py`** — pointed at PgBouncer instead of Postgres directly:
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'jobqueue_db',
        'USER': 'postgres',
        'PASSWORD': '',
        'HOST': 'localhost',
        'PORT': '6432',       # PgBouncer, not 5432
        'CONN_MAX_AGE': 0,    # let PgBouncer own pooling, not Django
    }
}
```

### 5.2 Verification before load testing

Confirmed PgBouncer was live and routing correctly via its admin interface:
```bash
psql -h 127.0.0.1 -p 6432 -U postgres -d pgbouncer -c "SHOW POOLS;"
```

### 5.3 Result — 150-user load test

| Metric | Before Fix | + CONN_MAX_AGE | + PgBouncer |
|---|---|---|---|
| Requests | 757 | 1,085 | **4,078** |
| Failures | 162 (21.4%) | 161 (14.8%) | **1 (0.0%)** |
| Avg latency | 18,848 ms | 11,631 ms | **2,085 ms** |
| p95 | 70,000 ms | 37,000 ms | **4,300 ms** |
| p99 | 98,000 ms | 49,000 ms | **12,000 ms** |
| RPS | 6.4 | 9.1 | **34.2** |
| **Peak connections** | 104 | 104 | **26** |

Postgres log for this run (`after_fix_pgbouncer_150_pg.log`) recorded **zero new entries** — no `FATAL` errors reached Postgres at all during the test.

### 5.4 Pool behavior confirmation

Sample from live `SHOW POOLS;` snapshots during a follow-up verification run:

```
  database   |   user    | cl_active | cl_waiting | sv_active | sv_idle | sv_used | pool_mode
-------------+-----------+-----------+------------+-----------+---------+---------+-------------
 jobqueue_db | postgres  |         5 |          0 |         0 |       7 |       9 | transaction
 jobqueue_db | postgres  |         5 |          0 |         0 |       3 |      13 | transaction
```

`sv_idle + sv_used` (real Postgres connections in use) stayed in the 10–20 range throughout, confirming PgBouncer capped real backend connections near the configured `default_pool_size = 20`, regardless of client-side (Django/Celery) load.

### 5.5 Interpretation

PgBouncer resolved the root cause directly: real Postgres connections were capped at the pooler layer, so no matter how many concurrent requests Django/Celery generated, Postgres itself never saw more than ~26 simultaneous connections — well under the 100 limit. This explains both the elimination of `FATAL` errors and the large throughput/latency improvement: requests were no longer queuing or failing on connection acquisition.

---

## 6. Summary

| Stage | Peak Connections | Failure Rate | RPS | Verdict |
|---|---|---|---|---|
| Before fix | 104 / 100 | 21.4% | 6.4 | Ceiling breached |
| + `CONN_MAX_AGE=60` | 104 / 100 | 14.8% | 9.1 | Improved efficiency, ceiling still breached |
| + PgBouncer | 26 / 100 | 0.0% | 34.2 | Root cause resolved |

**Key takeaway:** `CONN_MAX_AGE` reduces connection *churn* (how often connections open/close) but does not cap concurrent connection *count*. When concurrent demand from Django + Celery exceeds `max_connections`, only a connection pooler (PgBouncer) that enforces a hard cap at the pool layer resolves it structurally.

---

## 7. Evidence Index

| Artifact | Location |
|---|---|
| Before-fix Locust report | `before_fix/locust_150_01082026_v04.html` |
| Before-fix Postgres log | `before_fix/before_fix_150_pg.log` |
| Before-fix connections CSV | `before_fix/before_fix_150_connections.csv` |
| CONN_MAX_AGE Locust report | `after_fix/locust_150_..._v05.html` |
| CONN_MAX_AGE Postgres log | `after_fix/after_fix_150_pg.log` |
| CONN_MAX_AGE connections CSV | `after_fix/after_fix_150_connections.csv` |
| PgBouncer Locust report | `after_fix_pgbouncer/locust_150_pgbouncer_v06.html` |
| PgBouncer Postgres log | `after_fix_pgbouncer/after_fix_pgbouncer_150_pg.log` |
| PgBouncer connections CSV | `after_fix_pgbouncer/after_fix_pgbouncer_150_connections.csv` |
| PgBouncer pool verification | `after_fix_pgbouncer/pgbouncer_pools_verification.log` |

**Note on timestamps:** Locust HTML/CSV reports use UTC; all other logs (Postgres, terminal, connection CSVs) use IST (UTC+5:30). Add 5:30 to Locust UTC timestamps to correlate with system logs.

**Test windows (IST):**
- Before fix: 23:52:22 – 23:54:21 (2026-08-01)
- + CONN_MAX_AGE: 00:13:27 – 00:15:27 (2026-08-02)
- + PgBouncer: 19:27:24 – 19:29:23 (2026-08-02)
