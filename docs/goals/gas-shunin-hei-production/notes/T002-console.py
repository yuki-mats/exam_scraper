"""既存console APIの呼出し結果を保存する。認証値はメモリだけで扱う。"""
import argparse
import json
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("endpoint")
    parser.add_argument("output", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:8790")
    args = parser.parse_args()
    if not args.endpoint.startswith("/api/"):
        parser.error("既存の/api/経路を指定する")
    session = requests.Session()
    if args.request:
        value = json.loads(args.request.read_text())
        token_response = session.get(args.base + "/api/session", timeout=30)
        token_response.raise_for_status()
        token = token_response.json()["sessionToken"]
        response = session.post(
            args.base + args.endpoint, json=value,
            headers={"X-Review-Session": token, "Origin": args.base}, timeout=120,
        )
    else:
        response = session.get(args.base + args.endpoint, timeout=120)
    result = response.json()
    if isinstance(result, dict):
        result.pop("sessionToken", None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    keys = ("status", "error", "canStart", "canEvaluate", "canPublish", "runId", "jobId",
            "targetCount", "workItemCount", "isProductionWrite", "blockingWarnings")
    summary = {key: result[key] for key in keys if key in result}
    for key in ("run", "job"):
        if isinstance(result.get(key), dict):
            summary[key] = {k: result[key].get(k) for k in ("runId", "jobId", "status")
                            if k in result[key]}
    print(response.status_code, json.dumps(summary, ensure_ascii=False))
    response.raise_for_status()


if __name__ == "__main__":
    main()
