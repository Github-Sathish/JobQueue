import random
import csv
import os
from datetime import datetime, timezone, timedelta

from locust import HttpUser, task, between, events
from locust.runners import MasterRunner

IST = timezone(timedelta(hours=5, minutes=30))

#payloads
JOB_PAYLOADS = {
    'email_send' : {
        'to' : 'test@example.com',
        'subject': 'Load test email',
        'body' : 'This is a load test message'
    },
    'report_gen':{
        'report_type' : 'monthly',
        'start_date' : '2024-01-01',
        'end_date' : '2024-01-31'
    },
    'data_process': {
    'dataset': 'users',
    'operations': 'aggregate',
    'filters': {'active': True}
    },
    'image_resize': {
        'url': 'https://example.com/image.jpg',
        'size': '800x600'
    },
}


JOB_TYPES = list(JOB_PAYLOADS.keys())
PRIORITIES = ['high', 'default', 'low']


#raw metrics
REPORTS_DIR = os.path.join(os.path.dirname(__file__), 'reports')
_csv_writer = None
_csv_file = None


def _init_csv():
    global _csv_writer, _csv_file
    os.makedirs(REPORTS_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(REPORTS_DIR, f"raw_{ts}.csv")
    _csv_file = open(path, "w", newline="")
    _csv_writer = csv.writer(_csv_file)
    _csv_writer.writerow(['timestamp', 'method', 'name', 'response_time_ms', 'status', 'error'])
    print(f"  -> Raw metrics: {path}")



#User behaviour
class JpbQueueUser(HttpUser):
    wait_time = between(1,3)
    created_job_ids : list

    def on_start(self) -> None:
        self.created_job_ids = []


    #tasks----------
    @task(3)
    def create_job(self):
        job_type = random.choice(JOB_TYPES)
        payload = {
            "job_type": job_type,
            'payload': JOB_PAYLOADS[job_type],
            'priority': random.choice(PRIORITIES)
            }
        with self.client.post(
            "/jobs/",
            json=payload,
            headers={"Content-Type" : "application/json"},
            catch_response=True,
            name= "POST /jobs/",
        ) as resp:
            if resp.status_code == 201:
                job_id = resp.json().get('id')
                if job_id:
                    self.created_job_ids.append(job_id)
                    if len(self.created_job_ids) > 20:
                        self.created_job_ids.pop(0)
                resp.success()
            else:
                resp.failure(f"{resp.status_code} - {resp.text[:120]}")

    @task(5)
    def poll_job_status(self):
        """GET /jobs/{id}/ — most frequent; mimics real polling cadence."""
        if not self.created_job_ids:
            return
 
        job_id = random.choice(self.created_job_ids)
 
        with self.client.get(
            f"/jobs/{job_id}/",
            catch_response=True,
            name="GET /jobs/{id}/",
        ) as resp:
            if resp.status_code == 200:
                status = resp.json().get("status")
                if status in ("completed", "failed"):
                    self.created_job_ids.remove(job_id)
                resp.success()
            elif resp.status_code == 404:
                # Job cleaned up — expected
                self.created_job_ids.remove(job_id)
                resp.success()
            else:
                resp.failure(f"{resp.status_code}")
 
    @task(1)
    def get_stats(self):
        """GET /jobs/stats/ — dashboard / monitoring calls."""
        with self.client.get(
            "/jobs/stats/",
            catch_response=True,
            name="GET /jobs/stats/",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"{resp.status_code}")
 
    @task(1)
    def health_check(self):
        """GET /health/ — synthetic uptime monitor."""
        with self.client.get(
            "/health/",
            catch_response=True,
            name="GET /health/",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"{resp.status_code}")
 
 
# ── Event hooks ───────────────────────────────────────────────────────────────
 
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    if not isinstance(environment.runner, MasterRunner):
        _init_csv()
    print(f"\n{'='*52}")
    print(f"  Load test starting")
    print(f"  Target : {environment.host}")
    print(f"{'='*52}\n")
 
 
@events.request.add_listener
def on_request(request_type, name, response_time, response_length,
               exception, context, **kwargs):
    if _csv_writer is None:
        return
    status = "ok" if exception is None else "error"
    error = str(exception) if exception else ""
    _csv_writer.writerow([
        datetime.now(IST).isoformat(),   # ← just this line changes
        request_type,
        name,
        round(response_time, 2),
        status,
        error,
    ])
 
 
@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    if _csv_file:
        _csv_file.close()
 
    s = environment.stats.total
    failure_rate = (s.num_failures / s.num_requests * 100) if s.num_requests else 0
 
    print(f"\n{'='*52}")
    print(f"  Load test complete")
    print(f"  Requests  : {s.num_requests:,}")
    print(f"  Failures  : {s.num_failures:,}  ({failure_rate:.1f}%)")
    print(f"  Avg (ms)  : {s.avg_response_time:.0f}")
    print(f"  p95 (ms)  : {s.get_response_time_percentile(0.95):.0f}")
    print(f"  p99 (ms)  : {s.get_response_time_percentile(0.99):.0f}")
    print(f"  RPS       : {s.total_rps:.1f}")
    print(f"{'='*52}\n")