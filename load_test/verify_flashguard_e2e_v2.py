#!/usr/bin/env python3
"""
FlashGuard independent end-to-end verifier v2.

Flow:
1. Send exactly one reservation request per CSV buyer.
2. Record successful reservation/order IDs.
3. Print sample HTTP 400/500 response bodies for diagnosis.
4. Pay ONLY successful reservations using the Swagger-confirmed body:
      {"simulate_status": "SUCCESS"}
5. Verify inventory and admin metrics.
6. Verify reservation idempotency by repeating one successful reservation
   with the exact same idempotency key.

Uses only Python standard library.
"""

import argparse
import csv
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter


def request_json(method, url, payload=None, timeout=20):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    started = time.perf_counter()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = raw
            return r.status, body, time.perf_counter() - started, None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = raw
        return e.code, body, time.perf_counter() - started, None
    except Exception as e:
        return None, None, time.perf_counter() - started, str(e)


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


def extract_id(body):
    if isinstance(body, dict):
        for key in ("order_id", "reservation_id", "id"):
            if body.get(key):
                return str(body[key])
        for value in body.values():
            if isinstance(value, dict):
                found = extract_id(value)
                if found:
                    return found
    return None


def get_inventory(base, product_id):
    return request_json("GET", f"{base}/api/products/{product_id}/inventory")


def get_metrics(base):
    return request_json("GET", f"{base}/api/admin/metrics")


def reserve_one(base, buyer, timeout):
    return buyer, request_json(
        "POST",
        f"{base}/api/orders/reserve",
        buyer,
        timeout,
    )


def pay_one(base, order_id, timeout):
    # Confirmed directly from Swagger:
    # POST /api/orders/{order_id}/pay
    # Request body: {"simulate_status": "SUCCESS"}
    return order_id, request_json(
        "POST",
        f"{base}/api/orders/{order_id}/pay",
        {"simulate_status": "SUCCESS"},
        timeout,
    )


def pretty(obj):
    return json.dumps(obj, indent=2) if isinstance(obj, (dict, list)) else str(obj)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--product-id", type=int, required=True)
    ap.add_argument("--concurrency", type=int, default=1000)
    ap.add_argument("--payment-concurrency", type=int, default=100)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--timeout", type=float, default=20)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    buyers = load_buyers(args.csv, args.product_id)

    print("=" * 68)
    print("FLASHGUARD INDEPENDENT END-TO-END VERIFIER v2")
    print("=" * 68)
    print(f"Base URL:                 {base}")
    print(f"Product ID:               {args.product_id}")
    print(f"Buyers:                   {len(buyers)}")
    print(f"Reservation concurrency:  {args.concurrency}")
    print(f"Payment concurrency:      {args.payment_concurrency}")
    print("=" * 68)

    status, body, _, err = request_json("GET", f"{base}/api/health")
    print(f"Health check: HTTP {status}" if status else f"Health check ERROR: {err}")
    if status != 200:
        raise SystemExit("Backend health check failed.")

    status, inv, _, err = get_inventory(base, args.product_id)
    if status != 200:
        raise SystemExit(f"Inventory check failed: HTTP {status} {pretty(inv)}")

    initial_inventory = inv.get("available_inventory") if isinstance(inv, dict) else None
    print(f"Inventory before: HTTP {status}")
    print(f"Available before: {initial_inventory}")

    if initial_inventory != 100:
        print("WARNING: This benchmark expects initial inventory = 100.")

    # ---------------- RESERVATION ----------------
    print("\n" + "=" * 68)
    print("RESERVATION PHASE")
    print("=" * 68)

    successful = []
    status_counts = Counter()
    error_examples = {}
    latencies = []

    start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(reserve_one, base, buyer, args.timeout)
            for buyer in buyers
        ]

        completed = 0
        for future in as_completed(futures):
            buyer, (code, body, latency, err) = future.result()
            completed += 1

            if latency is not None:
                latencies.append(latency)

            if code is not None:
                status_counts[code] += 1
                if code in (400, 500, 502, 503, 504) and code not in error_examples:
                    error_examples[code] = {
                        "user_id": buyer["user_id"],
                        "response": body,
                    }

            if code is not None and 200 <= code < 300:
                order_id = extract_id(body)
                if order_id:
                    successful.append({
                        "buyer": buyer,
                        "order_id": order_id,
                        "response": body,
                    })

            if completed % 100 == 0 or completed == len(buyers):
                print(f"Completed {completed}/{len(buyers)}")

    elapsed = time.perf_counter() - start

    print(f"\nReservation wall-clock: {elapsed:.2f}s")
    print(f"Successful reservations: {len(successful)}")
    print(f"HTTP status breakdown:   {dict(sorted(status_counts.items()))}")

    if error_examples:
        print("\nReservation error samples:")
        for code in sorted(error_examples):
            print(f"\nHTTP {code} example:")
            print(f"User: {error_examples[code]['user_id']}")
            print(pretty(error_examples[code]["response"]))

    if len(successful) > 100:
        raise SystemExit("CRITICAL: MORE THAN 100 RESERVATIONS SUCCEEDED.")

    if not successful:
        raise SystemExit("No successful reservations; cannot perform payment test.")

    # ---------------- INVENTORY AFTER RESERVATION ----------------
    status, inv, _, err = get_inventory(base, args.product_id)
    available_after_reservation = (
        inv.get("available_inventory") if isinstance(inv, dict) else None
    )

    print("\n" + "=" * 68)
    print("INVENTORY AFTER RESERVATION")
    print("=" * 68)
    print(f"HTTP status:             {status}")
    print(f"Available inventory:     {available_after_reservation}")
    print(f"Successful <= 100:       {'PASS' if len(successful) <= 100 else 'FAIL'}")
    print(f"Negative inventory:      {'PASS' if available_after_reservation is not None and available_after_reservation >= 0 else 'FAIL'}")

    # ---------------- PAYMENT ----------------
    print("\n" + "=" * 68)
    print("PAYMENT PHASE")
    print("=" * 68)
    print(f"Orders to pay:            {len(successful)}")
    print('Payment body:             {"simulate_status": "SUCCESS"}')
    print("Only successfully reserved orders will be paid.")

    payment_success = []
    payment_status = Counter()
    payment_errors = {}

    payment_start = time.perf_counter()

    with ThreadPoolExecutor(max_workers=min(args.payment_concurrency, len(successful))) as pool:
        futures = [
            pool.submit(pay_one, base, item["order_id"], args.timeout)
            for item in successful
        ]

        completed = 0
        for future in as_completed(futures):
            order_id, (code, body, latency, err) = future.result()
            completed += 1

            if code is not None:
                payment_status[code] += 1

            if code is not None and 200 <= code < 300:
                payment_success.append({
                    "order_id": order_id,
                    "response": body,
                })
            elif code not in payment_errors:
                payment_errors[code] = body

            if completed % 25 == 0 or completed == len(successful):
                print(f"Payments completed {completed}/{len(successful)}")

    payment_elapsed = time.perf_counter() - payment_start

    print(f"\nPayment wall-clock:       {payment_elapsed:.2f}s")
    print(f"Payments successful:      {len(payment_success)}")
    print(f"Payments failed:          {len(successful) - len(payment_success)}")
    print(f"Payment status breakdown: {dict(sorted(payment_status.items(), key=lambda x: str(x[0])))}")

    if payment_errors:
        print("\nPayment error samples:")
        for code in list(payment_errors.keys())[:3]:
            print(f"\nHTTP {code}:")
            print(pretty(payment_errors[code]))

    # ---------------- FINAL STATE ----------------
    status, final_inv, _, err = get_inventory(base, args.product_id)
    status_m, metrics, _, err_m = get_metrics(base)

    final_available = (
        final_inv.get("available_inventory")
        if isinstance(final_inv, dict) else None
    )

    print("\n" + "=" * 68)
    print("FINAL STATE")
    print("=" * 68)
    print(f"Inventory endpoint:       HTTP {status}")
    print(f"Final available:          {final_available}")
    print(f"Metrics endpoint:         HTTP {status_m}")
    print(f"Admin metrics:")
    print(pretty(metrics))

    # ---------------- IDEMPOTENCY ----------------
    # Repeat one ORIGINAL reservation request using its same idempotency key.
    # This is a better idempotency test than repeating payment.
    print("\n" + "=" * 68)
    print("RESERVATION IDEMPOTENCY CHECK")
    print("=" * 68)

    test_item = successful[0]
    buyer = test_item["buyer"]

    before_status, before_inv, _, _ = get_inventory(base, args.product_id)

    dup_code, dup_body, _, _ = request_json(
        "POST",
        f"{base}/api/orders/reserve",
        buyer,
        args.timeout,
    )

    after_status, after_inv, _, _ = get_inventory(base, args.product_id)

    original_id = test_item["order_id"]
    duplicate_id = extract_id(dup_body)

    print(f"Original order/res ID:   {original_id}")
    print(f"Duplicate HTTP:          {dup_code}")
    print(f"Duplicate returned ID:   {duplicate_id}")
    print(f"Same order/res ID:       {'PASS' if duplicate_id == original_id else 'CHECK'}")
    print(f"Inventory unchanged:     {'PASS' if before_inv == after_inv else 'FAIL'}")

    # ---------------- VERDICT ----------------
    print("\n" + "=" * 68)
    print("FINAL VERDICT")
    print("=" * 68)

    no_reservation_5xx = not any(code >= 500 for code in status_counts)
    no_payment_5xx = not any(code >= 500 for code in payment_status)

    checks = {
        "Exactly 100 reservations": len(successful) == 100,
        "Exactly 100 payments": len(payment_success) == 100,
        "Final inventory = 0": final_available == 0,
        "No negative inventory": final_available is not None and final_available >= 0,
        "No reservation 5xx": no_reservation_5xx,
        "No payment 5xx": no_payment_5xx,
        "Idempotency same ID": duplicate_id == original_id,
        "Idempotency inventory unchanged": before_inv == after_inv,
    }

    for label, passed in checks.items():
        print(f"{label:<34} {'PASS' if passed else 'FAIL'}")

    overall = all(checks.values())
    print(f"\nOVERALL E2E: {'PASS' if overall else 'FAIL'}")


if __name__ == "__main__":
    main()
