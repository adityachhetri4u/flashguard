
import argparse
import asyncio
import csv
import json
import time
from pathlib import Path
import httpx

BASE_URL = "http://localhost:8000"
INVENTORY = 100

async def req(client, method, path, **kwargs):
    r = await client.request(method, BASE_URL + path, **kwargs)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--concurrency", type=int, default=10000)
    args = parser.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        buyers = list(csv.DictReader(f))

    if len(buyers) != 10000:
        raise RuntimeError(f"Expected 10,000 CSV rows, found {len(buyers)}")

    print("=" * 75)
    print("FLASHGUARD — 10,000 BUYER E2E TEST")
    print("=" * 75)
    print(f"CSV                  : {args.csv}")
    print(f"Total buyers         : {len(buyers):,}")
    print(f"Concurrency          : {args.concurrency:,}")
    print("=" * 75)

    limits = httpx.Limits(
        max_connections=args.concurrency + 100,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(connect=30, read=60, write=30, pool=60)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:

        # 1. Seed ONCE and use the returned product ID.
        print("\n[1/6] RESET / SEED")
        status, seed = await req(client, "POST", "/api/admin/seed")
        print("Seed status:", status)
        print("Seed response:", seed)

        if status >= 400 or not isinstance(seed, dict):
            raise RuntimeError("Seed failed.")

        product_id = int(seed["product_id"])
        print(f"Using newly seeded Product ID: {product_id}")

        # Override CSV product_id with the actual newly seeded product.
        for b in buyers:
            b["product_id"] = str(product_id)

        status, inv = await req(
            client, "GET", f"/api/products/{product_id}/inventory"
        )
        print("\nInventory BEFORE:")
        print(json.dumps(inv, indent=2))

        # 2. Reserve all 10k concurrently.
        print("\n[2/6] RESERVATION — 10,000 CONCURRENT REQUESTS")

        async def reserve(b):
            payload = {
                "user_id": b["user_id"],
                "product_id": product_id,
                "quantity": int(b["quantity"]),
                "idempotency_key": b["idempotency_key"],
            }
            try:
                r = await client.post(BASE_URL + "/api/orders/reserve", json=payload)
                try:
                    body = r.json()
                except Exception:
                    body = r.text
                return b["user_id"], r.status_code, body
            except Exception as e:
                return b["user_id"], "CLIENT_ERROR", repr(e)

        start = time.perf_counter()
        reservation_results = await asyncio.gather(
            *(reserve(b) for b in buyers)
        )
        reserve_time = time.perf_counter() - start

        counts = {}
        successful = []
        client_errors = []

        for user_id, status, body in reservation_results:
            counts[str(status)] = counts.get(str(status), 0) + 1
            if status == 200 and isinstance(body, dict):
                order_id = body.get("order_id")
                if order_id:
                    successful.append({
                        "user_id": user_id,
                        "order_id": order_id,
                        "reservation_id": body.get("id"),
                    })
            elif status == "CLIENT_ERROR":
                client_errors.append((user_id, status, body))

        print(f"Wall time             : {reserve_time:.2f}s")
        print(f"Requests sent         : {len(reservation_results):,}")
        print(f"Successful reservations: {len(successful):,}")
        print(f"Status breakdown      : {counts}")
        print(f"Client errors         : {len(client_errors):,}")

        for item in [
            x for x in reservation_results if x[1] != 200
        ][:10]:
            print("Sample:", item)

        # 3. Pay the successful reservations.
        print(f"\n[3/6] PAYMENT — {len(successful):,} SUCCESSFUL ORDERS")

        async def pay(order):
            try:
                r = await client.post(
                    BASE_URL + f"/api/orders/{order['order_id']}/pay",
                    json={"simulate_status": "SUCCESS"},
                )
                try:
                    body = r.json()
                except Exception:
                    body = r.text
                return order["order_id"], r.status_code, body
            except Exception as e:
                return order["order_id"], "CLIENT_ERROR", repr(e)

        pay_start = time.perf_counter()
        payment_results = await asyncio.gather(
            *(pay(o) for o in successful)
        )
        pay_time = time.perf_counter() - pay_start

        pay_counts = {}
        payment_success = []
        for order_id, status, body in payment_results:
            pay_counts[str(status)] = pay_counts.get(str(status), 0) + 1
            if status == 200:
                payment_success.append(order_id)

        print(f"Wall time             : {pay_time:.2f}s")
        print(f"Payment attempts      : {len(payment_results):,}")
        print(f"Successful payments   : {len(payment_success):,}")
        print(f"Status breakdown      : {pay_counts}")

        for item in [
            x for x in payment_results if x[1] != 200
        ][:10]:
            print("Payment failure:", item)

        # 4. Final state.
        print("\n[4/6] FINAL INVENTORY")
        _, final_inv = await req(
            client, "GET", f"/api/products/{product_id}/inventory"
        )
        print(json.dumps(final_inv, indent=2))

        print("\n[5/6] ADMIN METRICS")
        _, metrics = await req(client, "GET", "/api/admin/metrics")
        print(json.dumps(metrics, indent=2))

        # 5. Verdict.
        print("\n[6/6] FINAL VERDICT")
        print("=" * 75)

        available = final_inv.get("available") if isinstance(final_inv, dict) else None

        checks = {
            "Exactly 10,000 requests": len(reservation_results) == 10000,
            "Exactly 100 reservations": len(successful) == 100,
            "No reservation client errors": len(client_errors) == 0,
            "Exactly 100 successful payments": len(payment_success) == 100,
            "No payment failures": len(payment_results) == len(payment_success),
            "Final available inventory = 0": available == 0,
        }

        for name, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'} — {name}")

        print("\nOVERALL E2E TEST:", "PASS" if all(checks.values()) else "FAIL")

if __name__ == "__main__":
    asyncio.run(main())
