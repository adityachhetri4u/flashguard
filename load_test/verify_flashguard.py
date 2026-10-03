#!/usr/bin/env python3
"""
FlashGuard independent concurrency verifier.

Purpose:
- Send exactly N UNIQUE reservation requests from a CSV.
- No request looping/retries unless explicitly requested.
- Verify inventory never oversells.
- Show exact HTTP status breakdown and timeouts.
- Verify idempotency by replaying one successful request.
- Query inventory and metrics directly from FastAPI.

No third-party packages required.

Example:
    python verify_flashguard.py --csv flashguard_1000_buyers_product12.csv --product-id 12 --concurrency 100

Before running:
    1. Reset/seed Product 12 manually.
    2. Confirm Initial Inventory = 100 and Available = 100.
    3. Start FastAPI on http://localhost:8000.
"""

import argparse
import csv
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def post_json(url, payload, timeout):
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            elapsed = (time.perf_counter() - started) * 1000
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw
            return {
                "ok": True,
                "status": response.status,
                "latency_ms": elapsed,
                "body": parsed,
                "error": None,
            }
    except HTTPError as e:
        elapsed = (time.perf_counter() - started) * 1000
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw
        return {
            "ok": False,
            "status": e.code,
            "latency_ms": elapsed,
            "body": parsed,
            "error": None,
        }
    except Exception as e:
        elapsed = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "status": None,
            "latency_ms": elapsed,
            "body": None,
            "error": f"{type(e).__name__}: {e}",
        }


def get_json(url, timeout):
    req = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            try:
                return response.status, json.loads(raw)
            except Exception:
                return response.status, raw
    except HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def load_csv(path, product_id):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"user_id", "product_id", "quantity", "idempotency_key"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing columns: {sorted(missing)}")

        for row in reader:
            csv_product = int(row["product_id"])
            if csv_product != product_id:
                raise ValueError(
                    f"CSV product_id {csv_product} does not match --product-id {product_id}"
                )
            rows.append({
                "user_id": row["user_id"],
                "product_id": product_id,
                "quantity": int(row["quantity"]),
                "idempotency_key": row["idempotency_key"],
            })
    return rows


def find_numbers(obj, keys):
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and isinstance(v, (int, float)) and not isinstance(v, bool):
                found.append((k, v))
            found.extend(find_numbers(v, keys))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(find_numbers(item, keys))
    return found


def extract_available(body):
    keys = {
        "available_inventory",
        "available",
        "available_stock",
        "available_quantity",
        "stock_available",
    }
    vals = find_numbers(body, keys)
    return vals[0][1] if vals else None


def extract_id(body):
    keys = {
        "order_id",
        "reservation_id",
        "orderid",
        "reservationid",
    }

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k.lower() in keys:
                    return str(v)
                result = walk(v)
                if result is not None:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = walk(item)
                if result is not None:
                    return result
        return None

    return walk(body)


def print_json(label, body):
    print(f"\n{label}:")
    try:
        print(json.dumps(body, indent=2))
    except Exception:
        print(body)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--product-id", type=int, required=True)
    parser.add_argument("--initial-inventory", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--no-idempotency", action="store_true")
    args = parser.parse_args()

    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    rows = load_csv(args.csv, args.product_id)
    if not rows:
        raise SystemExit("CSV contains no buyers.")

    print("=" * 60)
    print("FLASHGUARD INDEPENDENT CONCURRENCY VERIFIER")
    print("=" * 60)
    print(f"Base URL:          {args.base_url}")
    print(f"Product ID:        {args.product_id}")
    print(f"CSV buyers:        {len(rows)}")
    print(f"Initial inventory: {args.initial_inventory}")
    print(f"Concurrency cap:   {args.concurrency}")
    print(f"Timeout/request:   {args.timeout}s")
    print()
    print("IMPORTANT: This script sends exactly one reservation request")
    print("per CSV row. It does NOT loop a request for a duration.")
    print("=" * 60)

    # Health check
    status, health = get_json(f"{args.base_url}/api/health", args.timeout)
    print(f"\nHealth check: HTTP {status}")
    if status != 200:
        print_json("Health response", health)
        raise SystemExit("Backend health check failed.")

    # Inventory before
    status, inventory_before = get_json(
        f"{args.base_url}/api/products/{args.product_id}/inventory",
        args.timeout,
    )
    print(f"Inventory before: HTTP {status}")
    available_before = extract_available(inventory_before)
    print(f"Available before: {available_before}")
    if available_before is not None and available_before != args.initial_inventory:
        raise SystemExit(
            f"Expected {args.initial_inventory} available before test, "
            f"but API reports {available_before}. Reset/seed first."
        )

    # Send exactly one request per buyer.
    reserve_url = f"{args.base_url}/api/orders/reserve"
    results = [None] * len(rows)

    test_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        future_to_index = {
            pool.submit(post_json, reserve_url, row, args.timeout): i
            for i, row in enumerate(rows)
        }
        completed = 0
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            try:
                results[i] = future.result()
            except Exception as e:
                results[i] = {
                    "ok": False,
                    "status": None,
                    "latency_ms": None,
                    "body": None,
                    "error": f"{type(e).__name__}: {e}",
                }
            completed += 1
            if completed % 100 == 0 or completed == len(rows):
                print(f"Completed {completed}/{len(rows)} requests")

    total_time = time.perf_counter() - test_started

    successes = [r for r in results if r["status"] is not None and 200 <= r["status"] < 300]
    business_rejections = [
        r for r in results
        if r["status"] in {400, 409, 422}
    ]
    server_errors = [
        r for r in results
        if r["status"] is not None and r["status"] >= 500
    ]
    other_http = [
        r for r in results
        if r["status"] is not None
        and not (200 <= r["status"] < 300)
        and r["status"] not in {400, 409, 422}
        and r["status"] < 500
    ]
    client_errors = [r for r in results if r["status"] is None]

    status_counts = {}
    for r in results:
        key = str(r["status"]) if r["status"] is not None else "CLIENT_ERROR"
        status_counts[key] = status_counts.get(key, 0) + 1

    latencies = [r["latency_ms"] for r in results if r["latency_ms"] is not None]
    successful_ids = []
    for r in successes:
        rid = extract_id(r["body"])
        if rid:
            successful_ids.append(rid)

    print("\n" + "=" * 60)
    print("RESERVATION RESULTS")
    print("=" * 60)
    print(f"Requests sent:       {len(results)}")
    print(f"Successful (2xx):    {len(successes)}")
    print(f"Business rejects:    {len(business_rejections)}")
    print(f"5xx server errors:   {len(server_errors)}")
    print(f"Other HTTP errors:   {len(other_http)}")
    print(f"Client/timeouts:     {len(client_errors)}")
    print(f"Wall-clock time:     {total_time:.2f}s")
    if latencies:
        print(f"Average latency:     {statistics.mean(latencies):.2f} ms")
        print(f"Max latency:         {max(latencies):.2f} ms")
    print("\nHTTP status breakdown:")
    for code, count in sorted(status_counts.items()):
        print(f"  {code}: {count}")

    # Inventory after
    status, inventory_after = get_json(
        f"{args.base_url}/api/products/{args.product_id}/inventory",
        args.timeout,
    )
    available_after = extract_available(inventory_after)
    print("\n" + "=" * 60)
    print("INVENTORY VERIFICATION")
    print("=" * 60)
    print(f"Inventory endpoint:  HTTP {status}")
    print(f"Available after:    {available_after}")

    oversell = len(successes) > args.initial_inventory
    negative = available_after is not None and available_after < 0
    impossible_state = (
        available_after is not None
        and available_after > args.initial_inventory
    )

    print(f"Successful <= 100:  {'PASS' if len(successes) <= args.initial_inventory else 'FAIL'}")
    print(f"Negative inventory:  {'FAIL' if negative else 'PASS'}")
    print(f"Oversell detected:   {'YES' if oversell else 'NO'}")
    print(
        f"Inventory <= initial:{'PASS' if not impossible_state else 'FAIL'}"
    )

    # Optional idempotency check.
    idempotency_pass = None
    if not args.no_idempotency and successes:
        first_index = next(
            i for i, r in enumerate(results)
            if r["status"] is not None and 200 <= r["status"] < 300
        )
        original = rows[first_index]
        original_result = results[first_index]
        original_id = extract_id(original_result["body"])

        before_dup_status, before_dup_body = get_json(
            f"{args.base_url}/api/products/{args.product_id}/inventory",
            args.timeout,
        )
        before_dup = extract_available(before_dup_body)

        duplicate_result = post_json(reserve_url, original, args.timeout)

        after_dup_status, after_dup_body = get_json(
            f"{args.base_url}/api/products/{args.product_id}/inventory",
            args.timeout,
        )
        after_dup = extract_available(after_dup_body)

        duplicate_id = extract_id(duplicate_result["body"])

        same_id = (
            original_id is not None
            and duplicate_id is not None
            and original_id == duplicate_id
        )
        inventory_unchanged = (
            before_dup is not None
            and after_dup is not None
            and before_dup == after_dup
        )

        idempotency_pass = inventory_unchanged and (
            same_id or duplicate_result["status"] in {200, 201, 202, 409}
        )

        print("\n" + "=" * 60)
        print("IDEMPOTENCY VERIFICATION")
        print("=" * 60)
        print(f"Original status:    HTTP {original_result['status']}")
        print(f"Duplicate status:   HTTP {duplicate_result['status']}")
        print(f"Original ID:        {original_id}")
        print(f"Duplicate ID:       {duplicate_id}")
        print(f"Inventory before:   {before_dup}")
        print(f"Inventory after:    {after_dup}")
        print(f"Same order/res ID:  {'PASS' if same_id else 'N/A/FAIL'}")
        print(f"Inventory unchanged:{'PASS' if inventory_unchanged else 'FAIL'}")
        print(f"Idempotency result: {'PASS' if idempotency_pass else 'FAIL'}")

    # Final verdict
    core_pass = (
        len(results) == len(rows)
        and len(successes) <= args.initial_inventory
        and not oversell
        and not negative
        and not impossible_state
        and len(server_errors) == 0
        and len(client_errors) == 0
    )

    print("\n" + "=" * 60)
    print("FINAL VERDICT")
    print("=" * 60)
    print(f"Exactly one request per CSV row: {'PASS' if len(results) == len(rows) else 'FAIL'}")
    print(f"No overselling:                   {'PASS' if not oversell else 'FAIL'}")
    print(f"No negative inventory:             {'PASS' if not negative else 'FAIL'}")
    print(f"No impossible inventory increase:  {'PASS' if not impossible_state else 'FAIL'}")
    print(f"No 5xx errors:                     {'PASS' if not server_errors else 'FAIL'}")
    print(f"No client/timeouts:                {'PASS' if not client_errors else 'FAIL'}")
    if idempotency_pass is not None:
        print(f"Idempotency:                       {'PASS' if idempotency_pass else 'FAIL'}")

    print("\nOVERALL:",
          "PASS" if core_pass and (idempotency_pass is not False) else "FAIL")

    if server_errors or client_errors:
        print("\nIMPORTANT: Errors were observed. Do NOT treat this as a clean")
        print("10,000-buyer scalability result until the error types are understood.")

    print_json("Final inventory response", inventory_after)


if __name__ == "__main__":
    main()
