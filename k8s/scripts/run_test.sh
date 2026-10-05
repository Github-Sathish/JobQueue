#!/bin/bash
# Runs one load test against the JobQueue deployment on EKS and collects all results into results/<name>/.
# Usage:   ./run_test.sh <name> <users> <spawn_rate> <seconds> [kill_after_seconds]
# Example: ./run_test.sh smoke 5 1 120              (name: lowercase letters, digits, hyphens)
#          ./run_test.sh kill-worker 100 20 120 60  (also force-deletes one worker pod 60 s after the Locust Job is created)
# Prerequisites: kubectl pointed at the cluster and namespace; running Deployments web, worker, postgres and redis;
# a pod named "monitor" (manifests/monitor.yaml); ConfigMaps "locustfile" and "monitors". Logged times are in IST (Asia/Kolkata).
set -u
if [ $# -lt 4 ]; then echo "Usage: $0 <name> <users> <spawn_rate> <seconds> [kill_after_seconds]"; exit 1; fi
NAME=$1; USERS=$2; RATE=$3; SECS=$4; KILL_AT=${5:-0}
OUT=results/$NAME
mkdir -p "$OUT"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LB=$(kubectl get svc web -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
START=$(date -u +%FT%TZ)
echo "== Start $START UTC | pre-check: $(curl -s http://$LB/health/)"
kubectl get deploy,hpa,pods -o wide > "$OUT/config_before.txt" 2>&1

# 1. loggers inside the monitor pod
kubectl exec monitor -- sh -c 'rm -f /tmp/reports/* /tmp/*.pid; PYTHONPATH=$PWD nohup python /tmp/log_connections.py /tmp/reports/conn.csv </dev/null >/dev/null 2>&1 & echo $! > /tmp/conn.pid; TZ=Asia/Kolkata PYTHONPATH=$PWD nohup python /tmp/log_queue_depth.py /tmp/reports/queue.csv </dev/null >/dev/null 2>&1 & echo $! > /tmp/queue.pid'

# 2. pod, node, replica and HPA samples every 15s
( while true; do
    ts=$(TZ=Asia/Kolkata date +%FT%T%:z)
    kubectl top pods --no-headers 2>/dev/null | awk -v ts="$ts" '{print ts","$1","$2","$3}' >> "$OUT/pod_resources.csv"
    kubectl top nodes --no-headers 2>/dev/null | awk -v ts="$ts" '{print ts","$1","$2","$3","$4","$5}' >> "$OUT/node_resources.csv"
    kubectl get deploy --no-headers 2>/dev/null | awk -v ts="$ts" '{print ts","$1","$2}' >> "$OUT/replicas.csv"
    kubectl get hpa --no-headers 2>/dev/null | awk -v ts="$ts" '{print ts","$1","$3","$4","$5","$6}' >> "$OUT/hpa.csv"
    sleep 15
  done ) &
TOP_PID=$!

# 3. Locust Job
kubectl delete job "locust-$NAME" --ignore-not-found > /dev/null
cat <<JOBEOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: locust-$NAME
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: locust
        image: locustio/locust:2.32.0
        command: ["sh", "-c"]
        args: ["cp /mnt/locust/locustfile.py /tmp/locustfile.py; locust -f /tmp/locustfile.py --headless -u $USERS -r $RATE -t ${SECS}s --host http://web --only-summary; sleep 1800"]
        resources:
          requests:
            cpu: 200m
            memory: 256Mi
        volumeMounts:
        - name: locustfile
          mountPath: /mnt/locust
      volumes:
      - name: locustfile
        configMap:
          name: locustfile
JOBEOF

if [ "$KILL_AT" -gt 0 ]; then
  ( sleep $KILL_AT; VICTIM=$(kubectl get pods -l app=worker -o name | head -1); echo "$(TZ=Asia/Kolkata date +%FT%T%:z) killing $VICTIM" > "$OUT/kill.txt"; kubectl delete $VICTIM --grace-period=0 --force >> "$OUT/kill.txt" 2>&1 ) &
fi

# 4. wait for Locust to finish (max ~25 min)
echo "== Running: $USERS users, $RATE/s spawn, ${SECS}s. Keep this tab open."
for i in $(seq 1 300); do
  if kubectl logs job/locust-$NAME 2>/dev/null | grep -q "Load test complete"; then break; fi
  R=$(kubectl get pod -l job-name=locust-$NAME -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null)
  if [ "$R" = "ImagePullBackOff" ] || [ "$R" = "ErrImagePull" ]; then echo "Locust image pull failed ($R)"; break; fi
  sleep 5
done
sleep 5

echo "== Waiting for queue to drain (max 15 min)"
for i in $(seq 1 60); do
  N=$(kubectl exec deploy/postgres -- psql -U postgres -d jobqueue_db -tAc "select count(*) from jobs_job where created_at > '$START' and status in ('pending','processing');" 2>/dev/null)
  echo "   unfinished jobs: $N"
  [ "$N" = "0" ] && break
  sleep 15
done
# 5. stop loggers and collect everything
kill $TOP_PID 2>/dev/null
kubectl exec monitor -- sh -c 'kill $(cat /tmp/conn.pid) $(cat /tmp/queue.pid) 2>/dev/null; sleep 1'
kubectl logs job/locust-$NAME > "$OUT/locust_summary.txt" 2>&1
LP=$(kubectl get pod -l job-name=locust-$NAME -o jsonpath='{.items[0].metadata.name}')
kubectl exec $LP -- sh -c 'cat /tmp/reports/raw_*.csv' > "$OUT/locust_raw.csv" 2>/dev/null
kubectl exec monitor -- cat /tmp/reports/conn.csv > "$OUT/conn.csv" 2>/dev/null
kubectl exec monitor -- cat /tmp/reports/queue.csv > "$OUT/queue.csv" 2>/dev/null
kubectl logs deploy/postgres --since-time="$START" > "$OUT/postgres.log" 2>&1
echo "too_many_clients_FATAL: $(grep -c 'too many clients' "$OUT/postgres.log")" > "$OUT/postgres_fatal.txt"
kubectl get events --sort-by=.lastTimestamp > "$OUT/events.txt" 2>&1
kubectl exec deploy/redis -- sh -c "redis-cli hlen unacked; redis-cli zcard unacked_index" > "$OUT/unacked.txt" 2>&1
kubectl get deploy,hpa,pods -o wide > "$OUT/config_after.txt" 2>&1

PSQL="kubectl exec deploy/postgres -- psql -U postgres -d jobqueue_db"
$PSQL -c "select status, count(*) from jobs_job where created_at > '$START' group by status order by status;" > "$OUT/db_summary.txt" 2>&1
$PSQL -c "select count(*) as dead_letter_total from jobs_deadletterjob;" >> "$OUT/db_summary.txt" 2>&1
$PSQL -c "select job_type, priority, count(*) as jobs, round(avg(extract(epoch from started_at-created_at))::numeric,2) as avg_queue_s, round((percentile_cont(0.95) within group (order by extract(epoch from started_at-created_at)))::numeric,2) as p95_queue_s, round(avg(extract(epoch from completed_at-started_at))::numeric,2) as avg_proc_s from jobs_job where created_at > '$START' and retry_count = 0 and started_at is not null group by job_type, priority order by job_type, priority;" >> "$OUT/db_summary.txt" 2>&1
$PSQL -c "select job_type, retry_count, count(*) as jobs from jobs_job where created_at > '$START' group by job_type, retry_count order by job_type, retry_count;" >> "$OUT/db_summary.txt" 2>&1
kubectl delete job "locust-$NAME" > /dev/null
tar czf "results_$NAME.tgz" -C results "$NAME"
echo "== Done. Results in $OUT and results_$NAME.tgz (download via Actions)"
tail -n 30 "$OUT/locust_summary.txt"; cat "$OUT/postgres_fatal.txt"; cat "$OUT/db_summary.txt"
