
import argparse
import asyncio
import csv
import json
import time
from pathlib import Path

import httpx

BASE_URL = "http://localhost:8000"
EXPECTED_INVENTORY = 100


async def request_json(client, method, path, **kwargs):
    r = await client.request(method, BASE_URL + path, **kwargs)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--product-id", type=int, default=22)
    parser.add_argument("--concurrency", type=int, default=10000)
    args = parser.parse_args()

    with open(args.csv, newline="", encoding="utf-8") as f:
        buyers = list(csv.DictReader(f))

    if len(buyers) != 10000:
        raise RuntimeError(f"Expected exactly 10,000 buyers, found {len(buyers)}")

    print("=" * 78)
    print("FLASHGUARD — PRODUCT 22 / 10,000-CONCURRENT E2E TEST")
    print("=" * 78)
    print(f"CSV                  : {args.csv}")
    print(f"Target Product ID    : {args.product_id}")
    print(f"Total buyers         : {len(buyers):,}")
    print(f"Concurrency          : {args.concurrency:,}")
    print("=" * 78)

    limits = httpx.Limits(
        max_connections=args.concurrency + 100,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(connect=30, read=60, write=30, pool=60)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:

        # ----------------------------------------------------------
        # 1. VERIFY TARGET PRODUCT — NO SEED
        # ----------------------------------------------------------
        print("\n[1/6] VERIFY PRODUCT 22")
        status, products = await request_json(client, "GET", "/api/products")

        if status >= 400:
            raise RuntimeError(f"Could not retrieve products: {products}")

        ids = []
        if isinstance(products, list):
            for p in products:
                try:
                    ids.append(int(p["id"]))
                except Exception:
                    pass

        if args.product_id not in ids:
            raise RuntimeError(
                f"Product {args.product_id} was not found. "
                f"Available product IDs: {ids}"
            )

        print(f"Product {args.product_id} found.")
        print("NOTE: No /admin/seed call was made, so Product 22 is preserved.")

        # ----------------------------------------------------------
        # 2. INVENTORY BEFORE
        # ----------------------------------------------------------
        status, inventory_before = await request_json(
            client,
            "GET",
            f"/api/products/{args.product_id}/inventory",
        )

        if status >= 400:
            raise RuntimeError(
                f"Could not read Product {args.product_id} inventory: "
                f"{inventory_before}"
            )

        print("\nInventory BEFORE:")
        print(json.dumps(inventory_before, indent=2))

        # ----------------------------------------------------------
        # 3. 10,000 CONCURRENT RESERVATIONS
        # ----------------------------------------------------------
        print("\n[2/6] RESERVATION — 10,000 CONCURRENT REQUESTS")

        async def reserve_one(buyer):
            payload = {
                "user_id": buyer["user_id"],
                "product_id": args.product_id,
                "quantity": int(buyer["quantity"]),
                "idempotency_key": buyer["idempotency_key"],
            }

            try:
                r = await client.post(
                    BASE_URL + "/api/orders/reserve",
                    json=payload,
                )
                try:
                    body = r.json()
                except Exception:
                    body = r.text

                return buyer["user_id"], r.status_code, body

            except Exception as e:
                return buyer["user_id"], "CLIENT_ERROR", repr(e)

        start = time.perf_counter()
        results = await asyncio.gather(
            *(reserve_one(buyer) for buyer in buyers)
        )
        reservation_time = time.perf_counter() - start

        status_counts = {}
        successful_orders = []
        client_errors = []
        business_rejects = []
        server_errors = []

        for user_id, status, body in results:
            key = str(status)
            status_counts[key] = status_counts.get(key, 0) + 1

            if status == 200 and isinstance(body, dict):
                order_id = body.get("order_id")
                if order_id:
                    successful_orders.append({
                        "user_id": user_id,
                        "order_id": order_id,
                        "reservation_id": body.get("id"),
                    })
            elif status == "CLIENT_ERROR":
                client_errors.append((user_id, status, body))
            elif isinstance(status, int) and 400 <= status < 500:
                business_rejects.append((user_id, status, body))
            elif isinstance(status, int) and status >= 500:
                server_errors.append((user_id, status, body))

        print(f"Wall time                  : {reservation_time:.2f}s")
        print(f"Requests sent              : {len(results):,}")
        print(f"Successful reservations   : {len(successful_orders):,}")
        print(f"Business rejects (4xx)    : {len(business_rejects):,}")
        print(f"Server errors (5xx)       : {len(server_errors):,}")
        print(f"Client/network errors     : {len(client_errors):,}")
        print(f"Status breakdown          : {status_counts}")

        if business_rejects:
            print("\nSample business rejections:")
            for item in business_rejects[:5]:
                print(item)

        if server_errors:
            print("\n!!! UNEXPECTED HTTP 5xx SAMPLES !!!")
            for item in server_errors[:20]:
                print(item)

        if client_errors:
            print("\n!!! CLIENT/NETWORK ERROR SAMPLES !!!")
            for item in client_errors[:10]:
                print(item)

        # ----------------------------------------------------------
        # 4. INVENTORY AFTER RESERVATION
        # ----------------------------------------------------------
        _, inventory_after_reserve = await request_json(
            client,
            "GET",
            f"/api/products/{args.product_id}/inventory",
        )

        print("\nInventory AFTER RESERVATION:")
        print(json.dumps(inventory_after_reserve, indent=2))

        # ----------------------------------------------------------
        # 5. PAYMENT FOR ALL SUCCESSFUL ORDERS
        # ----------------------------------------------------------
        print(
            f"\n[3/6] PAYMENT — "
            f"{len(successful_orders):,} SUCCESSFUL RESERVATIONS"
        )

        async def pay_one(order):
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

        payment_start = time.perf_counter()
        payment_results = await asyncio.gather(
            *(pay_one(order) for order in successful_orders)
        )
        payment_time = time.perf_counter() - payment_start

        payment_counts = {}
        successful_payments = []
        payment_errors = []

        for order_id, status, body in payment_results:
            key = str(status)
            payment_counts[key] = payment_counts.get(key, 0) + 1

            if status == 200:
                successful_payments.append(order_id)
            else:
                payment_errors.append((order_id, status, body))

        print(f"Payment wall time          : {payment_time:.2f}s")
        print(f"Payment attempts           : {len(payment_results):,}")
        print(f"Successful payments        : {len(successful_payments):,}")
        print(f"Payment errors             : {len(payment_errors):,}")
        print(f"Payment status breakdown   : {payment_counts}")

        if payment_errors:
            print("\nPayment error samples:")
            for item in payment_errors[:20]:
                print(item)

        # ----------------------------------------------------------
        # 6. FINAL INVENTORY + METRICS
        # ----------------------------------------------------------
        print("\n[4/6] FINAL INVENTORY")
        _, final_inventory = await request_json(
            client,
            "GET",
            f"/api/products/{args.product_id}/inventory",
        )
        print(json.dumps(final_inventory, indent=2))

        print("\n[5/6] ADMIN METRICS")
        _, metrics = await request_json(
            client,
            "GET",
            "/api/admin/metrics",
        )
        print(json.dumps(metrics, indent=2))

        # ----------------------------------------------------------
        # 7. FINAL VERDICT
        # ----------------------------------------------------------
        print("\n[6/6] FINAL VERDICT")
        print("=" * 78)

        available = (
            final_inventory.get("available_inventory")
            if isinstance(final_inventory, dict)
            else None
        )

        initial = metrics.get("initial_inventory") if isinstance(metrics, dict) else None
        metric_available = metrics.get("available_inventory") if isinstance(metrics, dict) else None
        reserved = metrics.get("reserved_inventory") if isinstance(metrics, dict) else None
        confirmed = metrics.get("confirmed_orders") if isinstance(metrics, dict) else None
        active = metrics.get("active_reservations") if isinstance(metrics, dict) else None
        payment_failures = metrics.get("payment_failures") if isinstance(metrics, dict) else None

        checks = {
            "Exactly 10,000 requests": len(results) == 10000,
            "Exactly 100 successful reservations": len(successful_orders) == 100,
            "No oversell": len(successful_orders) <= EXPECTED_INVENTORY,
            "No negative inventory": available is not None and available >= 0,
            "Exactly 100 successful payments": len(successful_payments) == 100,
            "No payment errors": len(payment_errors) == 0,
            "Final available inventory = 0": available == 0,
            "Final reserved inventory = 0": reserved == 0,
            "Confirmed orders = 100": confirmed == 100,
            "Active reservations = 0": active == 0,
            "Payment failures = 0": payment_failures == 0,
            "Inventory invariant":
                initial is not None
                and metric_available is not None
                and reserved is not None
                and confirmed is not None
                and metric_available + reserved + confirmed <= initial,
        }

        for name, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'} — {name}")

        business_pass = all(checks.values())
        clean_load = len(server_errors) == 0 and len(client_errors) == 0

        print("\n" + "=" * 78)
        print("BUSINESS CORRECTNESS:", "PASS" if business_pass else "FAIL")
        print("10K SERVER/NETWORK CLEAN:", "PASS" if clean_load else "FAIL")

        if business_pass and clean_load:
            print("OVERALL E2E TEST: PASS")
        elif business_pass:
            print("OVERALL E2E TEST: BUSINESS PASS / LOAD ERRORS DETECTED")
        else:
            print("OVERALL E2E TEST: FAIL")

        print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
