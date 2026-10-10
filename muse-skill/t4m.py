#!/usr/bin/env python3
"""Command-line client for the Telegram4Muse bridge (agent side). Stdlib only.

Environment:
  T4M_BRIDGE_URL    e.g. https://telegram4muse-bridge.fly.dev
  T4M_AGENT_TOKEN   the bridge's AGENT_TOKEN

Usage:
  t4m.py status                 is the phone connected, how many groups, job counts
  t4m.py chats                  groups the phone allows (ids and titles)
  t4m.py submit job.json        queue a job (use - to read JSON from stdin)
  t4m.py submit job.json --wait queue it and wait for the phone's result
  t4m.py job JOB_ID             show one job
  t4m.py wait JOB_ID [--timeout SECONDS]
  t4m.py jobs [--limit N]       recent jobs, newest first
  t4m.py cancel JOB_ID          cancel a job that hasn't run yet

Prints JSON. Exit code 0 on success, 1 on an API error or a failed job,
2 when waiting timed out with the job still queued or running.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

FINISHED = {"succeeded", "failed", "canceled", "expired"}


def call(method, path, body=None):
    base = os.environ.get("T4M_BRIDGE_URL", "").rstrip("/")
    token = os.environ.get("T4M_AGENT_TOKEN", "")
    if not base or not token:
        sys.exit("Set T4M_BRIDGE_URL and T4M_AGENT_TOKEN.")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as err:
        try:
            detail = json.load(err)
        except ValueError:
            detail = {"error": {"code": "http_error", "message": str(err)}}
        print(json.dumps(detail, indent=2))
        sys.exit(1)


def wait(job_id, timeout):
    deadline = time.time() + timeout
    while True:
        job = call("GET", f"/v1/jobs/{job_id}")
        if job["status"] in FINISHED or time.time() >= deadline:
            return job
        time.sleep(5)


def finish(job):
    print(json.dumps(job, indent=2, ensure_ascii=False))
    if job["status"] == "succeeded":
        sys.exit(0)
    sys.exit(1 if job["status"] in FINISHED else 2)


def main():
    parser = argparse.ArgumentParser(description="Telegram4Muse bridge client")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("chats")
    p = sub.add_parser("submit")
    p.add_argument("file")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=120)
    p = sub.add_parser("job")
    p.add_argument("id")
    p = sub.add_parser("wait")
    p.add_argument("id")
    p.add_argument("--timeout", type=int, default=120)
    p = sub.add_parser("jobs")
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("cancel")
    p.add_argument("id")
    args = parser.parse_args()

    if args.cmd == "status":
        out = call("GET", "/v1/status")
    elif args.cmd == "chats":
        out = call("GET", "/v1/chats")
    elif args.cmd == "submit":
        text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        job = call("POST", "/v1/jobs", json.loads(text))
        if args.wait:
            finish(wait(job["id"], args.timeout))
        out = job
    elif args.cmd == "job":
        out = call("GET", f"/v1/jobs/{args.id}")
    elif args.cmd == "wait":
        finish(wait(args.id, args.timeout))
    elif args.cmd == "jobs":
        out = call("GET", f"/v1/jobs?limit={args.limit}")
    else:
        out = call("POST", f"/v1/jobs/{args.id}/cancel")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
