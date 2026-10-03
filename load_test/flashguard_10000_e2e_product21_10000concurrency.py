
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
    parser.add_argument("--product-id", type=int, default=21)
    parser.add_argument("--concurrency", type=int, default=10000)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    with csv_path.open(newline="", encoding="utf-8") as f:
        buyers = list(csv.DictReader(f))

    if len(buyers) != 10000:
        raise RuntimeError(f"Expected exactly 10,000 buyers, found {len(buyers)}")

    print("=" * 78)
    print("FLASHGUARD — 10,000 BUYER E2E TEST")
    print("=" * 78)
    print(f"CSV                  : {csv_path}")
    print(f"Product ID           : {args.product_id}")
    print(f"Total buyers         : {len(buyers):,}")
    print(f"Concurrency          : {args.concurrency:,}")
    print(f"Expected inventory   : {EXPECTED_INVENTORY}")
    print("=" * 78)

    limits = httpx.Limits(
        max_connections=args.concurrency + 100,
        max_keepalive_connections=args.concurrency,
    )
    timeout = httpx.Timeout(connect=30, read=60, write=30, pool=60)

    async with httpx.AsyncClient(limits=limits, timeout=timeout) as client:

        # ----------------------------------------------------------
        # 1. RESET / SEED
        # ----------------------------------------------------------
        print("\n[1/6] RESET / SEED")

        seed_status, seed_body = await request_json(
            client, "POST", "/api/admin/seed"
        )

        print("Seed status:", seed_status)
        print("Seed response:", seed_body)

        if seed_status >= 400:
            raise RuntimeError("Seed/reset failed.")

        seeded_product_id = (
            seed_body.get("product_id")
            if isinstance(seed_body, dict)
            else None
        )

        if seeded_product_id != args.product_id:
            raise RuntimeError(
                f"Expected newly seeded Product {args.product_id}, "
                f"but backend returned Product {seeded_product_id}. "
                "Do not run the load test against the wrong product."
            )

        # ----------------------------------------------------------
        # 2. INVENTORY BEFORE
        # ----------------------------------------------------------
        inv_status, inventory_before = await request_json(
            client,
            "GET",
            f"/api/products/{args.product_id}/inventory",
        )

        print("\nInventory BEFORE:")
        print(json.dumps(inventory_before, indent=2))

        if inv_status >= 400:
            raise RuntimeError("Could not read inventory.")

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

        # Exactly 10,000 tasks are launched. With --concurrency 10000,
        # all 10,000 reservation requests are allowed to be in flight.
        results = await asyncio.gather(
            *(reserve_one(buyer) for buyer in buyers)
        )

        reservation_time = time.perf_counter() - start

        status_counts = {}
        successful_orders = []
        client_errors = []
        server_errors = []
        business_rejects = []

        for user_id, status, body in results:
            status_counts[str(status)] = status_counts.get(str(status), 0) + 1

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
            print("\nSample expected business rejections:")
            for item in business_rejects[:5]:
                print(item)

        if server_errors:
            print("\n!!! SAMPLE UNEXPECTED SERVER ERRORS !!!")
            for item in server_errors[:20]:
                print(item)

        if client_errors:
            print("\n!!! SAMPLE CLIENT/NETWORK ERRORS !!!")
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
        # 5. PAY ALL SUCCESSFUL RESERVATIONS
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
            payment_counts[str(status)] = (
                payment_counts.get(str(status), 0) + 1
            )

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
            print("\nSample payment errors:")
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

        metrics_initial = (
            metrics.get("initial_inventory")
            if isinstance(metrics, dict)
            else None
        )
        metrics_available = (
            metrics.get("available_inventory")
            if isinstance(metrics, dict)
            else None
        )
        confirmed = (
            metrics.get("confirmed_orders")
            if isinstance(metrics, dict)
            else None
        )
        reserved = (
            metrics.get("reserved_inventory")
            if isinstance(metrics, dict)
            else None
        )
        active = (
            metrics.get("active_reservations")
            if isinstance(metrics, dict)
            else None
        )
        payment_failures = (
            metrics.get("payment_failures")
            if isinstance(metrics, dict)
            else None
        )

        # Business correctness checks.
        checks = {
            "Exactly 10,000 requests": len(results) == 10000,
            "Exactly 100 successful reservations":
                len(successful_orders) == EXPECTED_INVENTORY,
            "No oversell":
                len(successful_orders) <= EXPECTED_INVENTORY,
            "No negative inventory":
                available is not None and available >= 0,
            "Exactly 100 successful payments":
                len(successful_payments) == EXPECTED_INVENTORY,
            "No payment errors":
                len(payment_errors) == 0,
            "Final available inventory = 0":
                available == 0,
            "Final reserved inventory = 0":
                reserved == 0,
            "Confirmed orders = 100":
                confirmed == EXPECTED_INVENTORY,
            "Active reservations = 0":
                active == 0,
            "Payment failures = 0":
                payment_failures == 0,
            "Inventory invariant":
                (
                    metrics_initial is not None
                    and metrics_available is not None
                    and confirmed is not None
                    and reserved is not None
                    and metrics_available + confirmed + reserved
                    <= metrics_initial
                ),
        }

        for name, passed in checks.items():
            print(f"{'PASS' if passed else 'FAIL'} — {name}")

        business_pass = all(checks.values())
        clean_transport = len(server_errors) == 0 and len(client_errors) == 0

        print("\n" + "=" * 78)
        print(
            "BUSINESS CORRECTNESS:",
            "PASS" if business_pass else "FAIL"
        )
        print(
            "10K TRANSPORT/SERVER CLEAN:",
            "PASS" if clean_transport else "FAIL"
        )
        print("=" * 78)

        if business_pass and clean_transport:
            print("OVERALL E2E TEST: PASS")
        elif business_pass:
            print(
                "OVERALL E2E TEST: BUSINESS PASS / "
                "SERVER LOAD ISSUES DETECTED"
            )
        else:
            print("OVERALL E2E TEST: FAIL")

if __name__ == "__main__":
    asyncio.run(main())
