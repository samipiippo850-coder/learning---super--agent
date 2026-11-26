"""Verify the exported dashboard is served by the API and all pages/assets resolve."""

from __future__ import annotations

import re
import sys

import httpx

BASE = "http://127.0.0.1:8000"


def main() -> int:
    client = httpx.Client(base_url=BASE, timeout=30)
    index = client.get("/")
    print("GET / ->", index.status_code, "bytes", len(index.text))
    if index.status_code != 200:
        print(index.text[:400])
        return 1
    title = re.search(r"<title>(.*?)</title>", index.text)
    print("title:", title.group(1) if title else "?")
    assets = re.findall(r'(?:src|href)="(/_next/[^"]+)"', index.text)
    print("assets referenced:", len(assets))
    failures = 0
    for asset in assets[:6]:
        response = client.get(asset)
        ok = response.status_code == 200
        failures += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} {response.status_code} {len(response.content):>8} {asset[:90]}")
    pages = ["/plan/", "/roi/", "/knowledge/", "/questions/", "/tests/", "/memory/", "/agent/", "/studio/", "/sources/"]
    for page in pages:
        response = client.get(page)
        ok = response.status_code == 200 and "ExamOS" in response.text
        failures += 0 if ok else 1
        print(f"  {'OK ' if ok else 'FAIL'} page {page} ({response.status_code})")
    for endpoint in ["/api/dashboard", "/health"]:
        response = client.get(endpoint)
        failures += 0 if response.status_code == 200 else 1
        print(f"  {'OK ' if response.status_code == 200 else 'FAIL'} api {endpoint} ({response.status_code})")
    print("FAILURES:", failures)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
