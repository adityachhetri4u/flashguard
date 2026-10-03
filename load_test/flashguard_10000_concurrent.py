#!/usr/bin/env python3
"""
FlashGuard 10,000-concurrent reservation stress tester.

Sends exactly ONE reservation request per CSV row.
All 10,000 requests are scheduled concurrently; there is no duration loop.

Requires:
    pip install httpx

Usage:
    python stress_10000.py --csv flashguard_10000_buyers_product17.csv --product-id 17

Optional:
    --concurrency 10000
    --timeout 30
    --base-url http://localhost:8000

Important:
"10,000 concurrent" means 10,000 client tasks are launched together.
Your OS/server may still limit actual simultaneous sockets. The script reports
timeouts/connection errors separately from HTTP application errors.
"""

import argparse
import asyncio
import csv
import json
import time
from collections import Counter

import httpx


def load_buyers(path, product_id):
    buyers = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            buyers.append({
                "user_id": row["user_id"],
                "product_id": int(row.get("product_id") or product_id),
                "quantity": int(row.get("quantity") or 1),
                "idempotency_key": row["idempotency_key"],
            })
    return buyers


def body_preview(response):
    try:
        return response.json()
    except Exception:
        return response.text[:500]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--product-id", type=int, required=True)
    parser.add_argument("--concurrency", type=int, default=10000)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    buyers = load_buyers(args.csv, args.product_id)

    if len(buyers) != 10000:
        raise SystemExit(f"Expected 10,000 buyers, found {len(buyers)}")

    print("=" * 72)
    print("FLASHGUARD INDEPENDENT 10,000-CONCURRENT STRESS TEST")
    print("=" * 72)
    print(f"Base URL:             {base}")
    print(f"Product ID:           {args.product_id}")
    print(f"CSV buyers:           {len(buyers)}")
    print(f"Requested concurrency:{args.concurrency}")
    print(f"Request timeout:      {args.timeout}s")
    print()
    print("Exactly one reservation request will be sent per CSV row.")
    print("There is NO duration loop and NO repeated request loop.")
    print("=" * 72)

    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(
        connect=args.timeout,
        read=args.timeout,
        write=args.timeout,
        pool=args.timeout,
    )

    async with httpx.AsyncClient(
        limits=limits,
        timeout=timeout,
        http2=False,
    ) as client:

        # Health check
        try:
            r = await client.get(f"{base}/api/health")
            print(f"Health check: HTTP {r.status_code}")
            if r.status_code != 200:
                raise SystemExit("Backend health check failed.")
        except Exception as e:
            raise SystemExit(f"Health check failed: {e}")

        # Inventory before
        try:
            r = await client.get(
                f"{base}/api/products/{args.product_id}/inventory"
            )
            before = r.json()
            initial_inventory = before.get("available_inventory")
            print(f"Inventory before: HTTP {r.status_code}")
            print(f"Available before: {initial_inventory}")
        except Exception as e:
            raise SystemExit(f"Initial inventory check failed: {e}")

        if initial_inventory != 100:
            print("WARNING: Expected initial inventory of 100.")

        status_counts = Counter()
        error_counts = Counter()
        error_samples = {}
        successful = []
        completed = 0
        lock = asyncio.Lock()

        async def reserve_one(buyer):
            nonlocal completed

            started = time.perf_counter()
            try:
                response = await client.post(
                    f"{base}/api/orders/reserve",
                    json=buyer,
                )
                latency = time.perf_counter() - started

                code = response.status_code
                body = body_preview(response)

                async with lock:
                    status_counts[code] += 1

                    if 200 <= code < 300:
                        successful.append({
                            "user_id": buyer["user_id"],
                            "response": body,
                            "latency": latency,
                        })
                    elif code >= 500:
                        error_counts[f"HTTP_{code}"] += 1
                        error_samples.setdefault(
                            f"HTTP_{code}",
                            (buyer["user_id"], body),
                        )
                    elif code >= 400:
                        error_counts[f"HTTP_{code}"] += 1
                        error_samples.setdefault(
                            f"HTTP_{code}",
                            (buyer["user_id"], body),
                        )

                    completed += 1
                    if completed % 500 == 0 or completed == len(buyers):
                        print(f"Completed {completed}/{len(buyers)}")

                return code

            except httpx.TimeoutException as e:
                async with lock:
                    error_counts["TIMEOUT"] += 1
                    error_samples.setdefault(
                        "TIMEOUT",
                        (buyer["user_id"], str(e)),
                    )
                    completed += 1
                return None

            except httpx.ConnectError as e:
                async with lock:
                    error_counts["CONNECTION_ERROR"] += 1
                    error_samples.setdefault(
                        "CONNECTION_ERROR",
                        (buyer["user_id"], str(e)),
                    )
                    completed += 1
                return None

            except Exception as e:
                async with lock:
                    error_counts[type(e).__name__] += 1
                    error_samples.setdefault(
                        type(e).__name__,
                        (buyer["user_id"], str(e)),
                    )
                    completed += 1
                return None

        print("\nStarting 10,000-request burst...")
        start = time.perf_counter()

        # Create exactly one task per buyer.
        tasks = [asyncio.create_task(reserve_one(b)) for b in buyers]
        await asyncio.gather(*tasks)

        elapsed = time.perf_counter() - start

        print("\n" + "=" * 72)
        print("RESERVATION RESULTS")
        print("=" * 72)
        print(f"Requests sent:        {len(buyers)}")
        print(f"Successful (2xx):     {len(successful)}")
        print(f"Wall-clock time:      {elapsed:.2f}s")
        print(f"Approx requests/sec:  {len(buyers) / elapsed:.2f}")
        print(f"Status breakdown:     {dict(sorted(status_counts.items()))}")
        print(f"Error breakdown:      {dict(sorted(error_counts.items()))}")

        if error_samples:
            print("\nERROR SAMPLES (first sample per category):")
            for category, (user, body) in error_samples.items():
                print(f"\n[{category}] user={user}")
                print(json.dumps(body, indent=2) if isinstance(body, (dict, list)) else body)

        # Inventory after
        try:
            r = await client.get(
                f"{base}/api/products/{args.product_id}/inventory"
            )
            after = r.json()
            available_after = after.get("available_inventory")
            print("\n" + "=" * 72)
            print("INVENTORY VERIFICATION")
            print("=" * 72)
            print(f"Inventory endpoint:   HTTP {r.status_code}")
            print(f"Available after:      {available_after}")
            print(f"Successful <= 100:    {'PASS' if len(successful) <= 100 else 'FAIL'}")
            print(f"Negative inventory:   {'PASS' if available_after is not None and available_after >= 0 else 'FAIL'}")
            print(f"Inventory <= initial: {'PASS' if available_after is not None and available_after <= 100 else 'FAIL'}")
            print(f"Oversell detected:    {'NO' if len(successful) <= 100 else 'YES'}")
        except Exception as e:
            print(f"Final inventory check failed: {e}")
            available_after = None

        # Metrics
        try:
            r = await client.get(f"{base}/api/admin/metrics")
            metrics = r.json()
            print("\nADMIN METRICS")
            print(json.dumps(metrics, indent=2))
        except Exception as e:
            print(f"Metrics check failed: {e}")
            metrics = None

        # Verdict intentionally distinguishes business correctness from transport errors.
        no_oversell = len(successful) <= 100
        inventory_safe = (
            available_after is not None
            and 0 <= available_after <= 100
        )
        no_server_5xx = not any(
            code >= 500 for code in status_counts
        )

        print("\n" + "=" * 72)
        print("FINAL VERDICT")
        print("=" * 72)
        print(f"Exactly one request per CSV row: PASS")
        print(f"No overselling:                 {'PASS' if no_oversell else 'FAIL'}")
        print(f"Inventory invariant:            {'PASS' if inventory_safe else 'FAIL'}")
        print(f"No HTTP 5xx:                    {'PASS' if no_server_5xx else 'FAIL'}")
        print(f"Successful reservations:        {len(successful)}")

        # A clean stress test requires both business correctness and zero 5xx/client errors.
        transport_clean = (
            not error_counts
            and no_server_5xx
        )
        overall = (
            len(successful) == 100
            and no_oversell
            and inventory_safe
            and transport_clean
        )

        print(f"\nOVERALL CLEAN STRESS TEST: {'PASS' if overall else 'FAIL'}")

        if len(successful) == 100 and no_oversell and inventory_safe:
            print("\nBusiness invariant result: PASS")
        else:
            print("\nBusiness invariant result: FAIL")


if __name__ == "__main__":
    asyncio.run(main())
