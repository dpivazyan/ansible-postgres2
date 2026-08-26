#!/usr/bin/env python3
"""
Simulates an incoming provisioning request: launches the Job Template
with target_ip as an extra_var. No AWX inventory objects are created or
touched — deploy_postgres.yml builds the target in-memory via add_host.
This stands in for what the real orchestrator will do later.

Usage:
    python3 test_trigger.py \
        --awx-url https://AWX_HOST \
        --token AWX_API_TOKEN \
        --job-template-id 7 \
        --ip 10.0.5.50 \
        --pg-version 16 \
        --pg-password 'SomeTestPass123!'
"""

import argparse
import sys
import time

import requests

def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--awx-url", required=True, help="e.g. https://10.0.1.10")
    p.add_argument("--token", required=True, help="AWX API token (Users > Tokens)")
    p.add_argument("--job-template-id", required=True, type=int)
    p.add_argument("--ip", required=True, help="Target VM IP — passed through as target_ip extra_var")
    p.add_argument("--pg-version", required=True)
    p.add_argument("--pg-password", required=True)
    p.add_argument("--pg-tier", default="small")
    p.add_argument("--pg-use-separate-data-disk", default="false")
    p.add_argument("--insecure", action="store_true", help="skip TLS verification (self-signed certs)")
    return p.parse_args()


def main():
    args = parse_args()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {args.token}"})
    session.verify = not args.insecure

    base = args.awx_url.rstrip("/") + "/api/v2"

    print(f"[1/2] Launching Job Template {args.job_template_id} for target_ip={args.ip}...")
    launch_payload = {
        "extra_vars": {
            "target_ip": args.ip,
            "pg_version": args.pg_version,
            "pg_app_db_password": args.pg_password,
            "pg_tier": args.pg_tier,
            "pg_use_separate_data_disk": args.pg_use_separate_data_disk,
        },
    }
    resp = session.post(f"{base}/job_templates/{args.job_template_id}/launch/", json=launch_payload)
    resp.raise_for_status()
    job_id = resp.json()["job"]
    print(f"      Job launched: id={job_id}  (view at {args.awx_url}/#/jobs/playbook/{job_id}/output)")

    print(f"[2/2] Polling job {job_id} for completion...")
    terminal_states = {"successful", "failed", "error", "canceled"}
    while True:
        resp = session.get(f"{base}/jobs/{job_id}/")
        resp.raise_for_status()
        status = resp.json()["status"]
        print(f"      status: {status}")
        if status in terminal_states:
            break
        time.sleep(5)

    if status != "successful":
        print(f"\nJob did NOT succeed (status={status}). Check the AWX job output for details.")
        sys.exit(1)

    print(f"\nDeployment succeeded — Postgres {args.pg_version} should be live on {args.ip}.")


if __name__ == "__main__":
    main()

