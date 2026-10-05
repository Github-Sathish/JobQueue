import csv
import sys
import time
from datetime import datetime

import redis

if len(sys.argv) != 2:
    print("Usage:")
    print("python log_queue_depth.py output.csv")
    sys.exit(1)

outfile = sys.argv[1]

# Adjust host/port/db if your Redis isn't on localhost:6379/db0
r = redis.Redis(host="localhost", port=6379, db=0)

QUEUES = ["jobs.high", "jobs.default", "jobs.low"]

print(f"Logging Redis queue depths to '{outfile}'")
print("Press Ctrl+C to stop...\n")

with open(outfile, "w", newline="") as csvfile:

    writer = csv.writer(csvfile)
    writer.writerow(["timestamp"] + QUEUES + ["total_queued"])

    try:
        while True:
            depths = [r.llen(q) for q in QUEUES]

            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                *depths,
                sum(depths),
            ])

            csvfile.flush()
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nQueue depth logging stopped.")