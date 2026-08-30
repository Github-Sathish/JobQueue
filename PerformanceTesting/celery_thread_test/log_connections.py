import csv
import os
import sys
import time
from datetime import datetime

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from django.db import connection


if len(sys.argv) != 2:
    print("Usage:")
    print("python log_connections.py output.csv")
    sys.exit(1)

outfile = sys.argv[1]

print(f"Logging PostgreSQL connection statistics to '{outfile}'")
print("Press Ctrl+C to stop...\n")

# Static: max_connections doesn't change mid-run, fetch once
with connection.cursor() as cursor:
    cursor.execute("SHOW max_connections;")
    MAX_CONNECTIONS = int(cursor.fetchone()[0])

# NOTE: filtering by datname = current_database() so counts reflect
# only this app's DB, not other databases sharing the same Postgres instance.
QUERY = """
SELECT
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE state = 'active') AS active,
    COUNT(*) FILTER (WHERE state = 'idle') AS idle,
    COUNT(*) FILTER (WHERE wait_event IS NOT NULL) AS waiting,
    COUNT(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction,
    COALESCE(MAX(EXTRACT(EPOCH FROM (now() - query_start)))
        FILTER (WHERE state = 'active'), 0) AS longest_active_query_secs,
    COALESCE(MAX(EXTRACT(EPOCH FROM (now() - xact_start)))
        FILTER (WHERE state != 'idle'), 0) AS longest_transaction_secs
FROM pg_stat_activity
WHERE datname = current_database();
"""

LOCK_QUERY = """
SELECT COUNT(*) FROM pg_locks WHERE NOT granted;
"""

DB_STAT_QUERY = """
SELECT deadlocks, temp_files, temp_bytes
FROM pg_stat_database
WHERE datname = current_database();
"""

with open(outfile, "w", newline="") as csvfile:

    writer = csv.writer(csvfile)

    writer.writerow([
        "timestamp",
        "total_connections",
        "pct_of_max",
        "active",
        "idle",
        "waiting",
        "idle_in_transaction",
        "longest_active_query_secs",
        "longest_transaction_secs",
        "locks_waiting",
        "deadlocks_cumulative",
        "temp_files_cumulative",
        "temp_bytes_cumulative",
    ])

    try:
        while True:

            with connection.cursor() as cursor:
                cursor.execute(QUERY)
                (total, active, idle, waiting, idle_tx,
                 longest_query, longest_txn) = cursor.fetchone()

                cursor.execute(LOCK_QUERY)
                (locks_waiting,) = cursor.fetchone()

                cursor.execute(DB_STAT_QUERY)
                (deadlocks, temp_files, temp_bytes) = cursor.fetchone()

            pct_of_max = round((total / MAX_CONNECTIONS) * 100, 1)

            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                total,
                pct_of_max,
                active,
                idle,
                waiting,
                idle_tx,
                round(longest_query, 2),
                round(longest_txn, 2),
                locks_waiting,
                deadlocks,
                temp_files,
                temp_bytes,
            ])

            csvfile.flush()

            time.sleep(1)

    except KeyboardInterrupt:
        print("\nConnection logging stopped.")