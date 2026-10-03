import asyncio
import httpx
import argparse
import csv
import json
import time
from datetime import datetime
import os
import sys

# Define color codes for pretty output
RESET = "\033[0m"
BOLD = "\033[1m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"

def print_header(title):
    print(f"\n{BOLD}{CYAN}{'='*40}")
    print(f"{title}")
    print(f"{'='*40}{RESET}")

def generate_test_data(filename, product_id, num_users=200):
    print(f"Generating test data: {filename} for {num_users} users (product {product_id})...")
    with open(filename, mode='w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(["user_id", "product_id", "quantity", "idempotency_key"])
        for i in range(1, num_users + 1):
            writer.writerow([
                f"flash_user_{i:03d}",
                product_id,
                1,
                f"flash_user_{i:03d}_req_001"
            ])
    print("Test data generated.")

async def make_reservation(client, url, row):
    start_time = time.time()
    payload = {
        "user_id": row["user_id"],
        "product_id": int(row["product_id"]),
        "quantity": int(row["quantity"]),
        "idempotency_key": row["idempotency_key"]
    }
    
    try:
        response = await client.post(url, json=payload, timeout=60.0)
        latency = (time.time() - start_time) * 1000
        
        status_code = response.status_code
        try:
            body = response.json()
        except:
            body = response.text
            
        return {
            "user_id": row["user_id"],
            "idempotency_key": row["idempotency_key"],
            "status_code": status_code,
            "body": body,
            "latency": latency,
            "error": None
        }
    except Exception as e:
        latency = (time.time() - start_time) * 1000
        return {
            "user_id": row["user_id"],
            "idempotency_key": row["idempotency_key"],
            "status_code": 0,
            "body": None,
            "latency": latency,
            "error": str(e)
        }

async def make_payment(client, url, semaphore, order_id, reservation_id):
    async with semaphore:
        start_time = time.time()
        payload = {
            "simulate_status": "SUCCESS"
        }
        try:
            response = await client.post(url, json=payload, timeout=60.0)
            latency = (time.time() - start_time) * 1000
            status_code = response.status_code
            try:
                body = response.json()
            except:
                body = response.text
            return {
                "order_id": order_id,
                "reservation_id": reservation_id,
                "status_code": status_code,
                "body": body,
                "latency": latency,
                "error": None
            }
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return {
                "order_id": order_id,
                "reservation_id": reservation_id,
                "status_code": 0,
                "body": None,
                "latency": latency,
                "error": str(e)
            }

async def fetch_inventory(client, base_url, product_id):
    url = f"{base_url}/api/products/{product_id}/inventory"
    response = await client.get(url, timeout=30.0)
    if response.status_code == 200:
        return response.json()
    return None

async def fetch_metrics(client, base_url):
    url = f"{base_url}/api/admin/metrics"
    response = await client.get(url, timeout=30.0)
    if response.status_code == 200:
        return response.json()
    return None

async def run_test(args):
    product_id = args.product_id
    base_url = args.base_url.rstrip("/")
    csv_file = args.csv_file
    
    if not os.path.exists(csv_file):
        generate_test_data(csv_file, product_id, num_users=200)
    
    users = []
    with open(csv_file, mode='r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row["product_id"]) == str(product_id):
                users.append(row)
    
    total_buyers = len(users)
    print_header("FLASHGUARD FLASH-SALE TEST")
    print(f"Product ID:             {product_id}")
    print(f"Total Buyers (CSV):     {total_buyers}")
    
    # Pre-test metrics
    async with httpx.AsyncClient(timeout=30.0) as client:
        init_metrics = await fetch_metrics(client, base_url)
        if init_metrics:
            init_inventory = init_metrics.get("initial_inventory", 100)
            available_inv = init_metrics.get("available_inventory", 100)
        else:
            init_inventory = 100
            available_inv = 100
            
    print(f"Initial Inventory:      {init_inventory}")
    print(f"Available Inventory:    {available_inv}")
    
    if available_inv == 0:
        print(f"\n{YELLOW}Warning: Available inventory is already 0. Did you forget to reset/seed?{RESET}")

    print("\n--- RESERVATION PHASE ---")
    
    reserve_url = f"{base_url}/api/orders/reserve"
    results = []
    
    # Phase 1: Reservation
    async with httpx.AsyncClient(limits=httpx.Limits(max_connections=10000, max_keepalive_connections=10000), timeout=120.0) as client:
        tasks = [make_reservation(client, reserve_url, u) for u in users]
        print(f"Firing {len(tasks)} concurrent reservation requests...")
        results = await asyncio.gather(*tasks)

    # Phase 2: Classify
    successful_reservations = []
    rejected_reservations = []
    http_errors = []
    latencies = []
    
    for r in results:
        latencies.append(r["latency"])
        if r["error"] or r["status_code"] >= 500:
            http_errors.append(r)
        elif r["status_code"] == 200:
            body = r["body"]
            if body and body.get("status") == "RESERVED" and body.get("order_id"):
                successful_reservations.append(r)
            else:
                http_errors.append(r) # 200 but unexpected body
        elif r["status_code"] == 400:
            body = r["body"]
            if isinstance(body, dict) and body.get("detail") == "OUT_OF_STOCK":
                rejected_reservations.append(r)
            else:
                http_errors.append(r)
        else:
            http_errors.append(r)

    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    latencies.sort()
    p95_latency = latencies[int(len(latencies) * 0.95)] if latencies else 0

    success_count = len(successful_reservations)
    reject_count = len(rejected_reservations)
    error_count = len(http_errors)
    
    oversold = "YES" if success_count > 100 else "NO"
    oversold_color = RED if oversold == "YES" else GREEN

    print(f"Reservation Requests:   {total_buyers}")
    print(f"Successful:             {success_count}")
    print(f"Rejected (Out of Stock):{reject_count}")
    print(f"HTTP Errors/Other:      {error_count}")
    print(f"Average Latency:         {avg_latency:.2f} ms")
    print(f"P95 Latency:             {p95_latency:.2f} ms")

    # Phase 3: Inventory Check
    print("\n--- INVENTORY CHECK ---")
    async with httpx.AsyncClient(timeout=30.0) as client:
        inv_data = await fetch_inventory(client, base_url, product_id)
        metrics = await fetch_metrics(client, base_url)
        
    current_available = inv_data.get("available_inventory") if inv_data else "UNKNOWN"
    
    negative_inv = "YES" if isinstance(current_available, int) and current_available < 0 else "NO"
    neg_color = RED if negative_inv == "YES" else GREEN
    
    invariant_pass = False
    if isinstance(current_available, int) and 0 <= current_available <= init_inventory:
        if current_available == max(0, init_inventory - success_count):
            invariant_pass = True
            
    inv_pass_str = "PASS" if invariant_pass else "FAIL"
    inv_pass_color = GREEN if invariant_pass else RED

    print(f"Available Inventory:      {current_available}")
    print(f"Oversell Detected:       {oversold_color}{oversold}{RESET}")
    print(f"Negative Inventory:      {neg_color}{negative_inv}{RESET}")
    print(f"Inventory Invariant:     {inv_pass_color}{inv_pass_str}{RESET}")
    
    if not invariant_pass or oversold == "YES" or negative_inv == "YES":
        print(f"\n{RED}TEST FAILED: Inventory constraint violated.{RESET}")
        save_results(args, locals())
        sys.exit(1)

    # Phase 4: Payment
    print("\n--- PAYMENT PHASE ---")
    orders_to_pay = [r["body"]["order_id"] for r in successful_reservations if r.get("body")]
    reservations_to_pay = [r["body"]["id"] for r in successful_reservations if r.get("body")]
    print(f"Orders to Pay:           {len(orders_to_pay)}")
    
    payment_results = []
    if orders_to_pay:
        sem = asyncio.Semaphore(25) # Max 25 concurrent payment requests
        async with httpx.AsyncClient(limits=httpx.Limits(max_connections=100), timeout=60.0) as client:
            tasks = []
            for r in successful_reservations:
                order_id = r["body"]["order_id"]
                res_id = r["body"]["id"]
                pay_url = f"{base_url}/api/orders/{order_id}/pay"
                tasks.append(make_payment(client, pay_url, sem, order_id, res_id))
            
            print(f"Processing payments...")
            payment_results = await asyncio.gather(*tasks)

    payments_successful = 0
    payments_failed = 0
    
    for pr in payment_results:
        if pr["status_code"] == 200 and pr.get("body") and pr["body"].get("status") == "SUCCESS":
            payments_successful += 1
        else:
            payments_failed += 1

    print(f"Payments Successful:     {payments_successful}")
    print(f"Payments Failed:           {payments_failed}")
    print(f"Confirmed Orders:        {payments_successful}")

    # Phase 5: Final State
    print("\n--- FINAL INVENTORY ---")
    async with httpx.AsyncClient(timeout=30.0) as client:
        final_inv_data = await fetch_inventory(client, base_url, product_id)
        final_metrics = await fetch_metrics(client, base_url)
    
    final_available = final_inv_data.get("available_inventory") if final_inv_data else "UNKNOWN"
    confirmed_orders_metric = final_metrics.get("confirmed_orders", "UNKNOWN") if final_metrics else "UNKNOWN"
    
    print(f"Initial:                 {init_inventory}")
    print(f"Available:                 {final_available}")
    print(f"Confirmed:               {confirmed_orders_metric}")
    
    # Phase 6: Idempotency Check
    print("\n--- IDEMPOTENCY ---")
    idempotency_pass = "N/A"
    dup_order = "N/A"
    extra_deduct = "N/A"
    
    if successful_reservations:
        test_req = successful_reservations[0]
        test_payload = {
            "user_id": test_req["user_id"],
            "product_id": int(product_id),
            "quantity": 1,
            "idempotency_key": test_req["idempotency_key"]
        }
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Fetch current inventory
            inv_before = await fetch_inventory(client, base_url, product_id)
            avail_before = inv_before.get("available_inventory")
            
            # 2. Resend the exact same reservation
            dup_resp = await client.post(reserve_url, json=test_payload)
            dup_status = dup_resp.status_code
            
            # 3. Check inventory again
            inv_after = await fetch_inventory(client, base_url, product_id)
            avail_after = inv_after.get("available_inventory")
            
            if dup_status == 200:
                dup_body = dup_resp.json()
                if dup_body.get("id") == test_req["body"]["id"] and avail_before == avail_after:
                    idempotency_pass = "PASS"
                    dup_order = "NO"
                    extra_deduct = "NO"
                else:
                    idempotency_pass = "FAIL (Returned different reservation or deducted inv)"
                    dup_order = "YES" if dup_body.get("id") != test_req["body"]["id"] else "NO"
                    extra_deduct = "YES" if avail_before != avail_after else "NO"
            else:
                idempotency_pass = f"FAIL (Status {dup_status})"
                dup_order = "UNKNOWN"
                extra_deduct = "YES" if avail_before != avail_after else "NO"
    else:
        idempotency_pass = "SKIP (No successful reservations)"
        
    idem_color = GREEN if "PASS" in idempotency_pass else (YELLOW if "SKIP" in idempotency_pass else RED)
    
    print(f"Duplicate Request:       {idem_color}{idempotency_pass}{RESET}")
    print(f"Duplicate Order:         {dup_order}")
    print(f"Extra Inventory Deducted: {extra_deduct}")
    
    print_header(f"FINAL RESULT: {GREEN}PASS{RESET}" if (invariant_pass and "PASS" in idempotency_pass and oversold == "NO") else f"FINAL RESULT: {RED}FAIL{RESET}")

    save_results(args, locals())

def save_results(args, context):
    results_dir = os.path.join(os.path.dirname(__file__), "results")
    os.makedirs(results_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(results_dir, f"flash_sale_{context['total_buyers']}_{timestamp}.json")
    txt_path = os.path.join(results_dir, f"flash_sale_{context['total_buyers']}_{timestamp}.txt")
    
    # Save JSON
    data = {
        "timestamp": timestamp,
        "product_id": args.product_id,
        "total_buyers": context['total_buyers'],
        "reservation_phase": {
            "requests": context['total_buyers'],
            "successful": context['success_count'],
            "rejected": context['reject_count'],
            "errors": context['error_count'],
            "avg_latency_ms": context.get('avg_latency', 0),
            "p95_latency_ms": context.get('p95_latency', 0),
        },
        "inventory_check": {
            "available_after_reserve": context.get('current_available'),
            "oversold": context.get('oversold'),
            "negative_inventory": context.get('negative_inv'),
            "invariant_pass": context.get('invariant_pass')
        },
        "payment_phase": {
            "attempted": len(context.get('orders_to_pay', [])),
            "successful": context.get('payments_successful', 0),
            "failed": context.get('payments_failed', 0)
        },
        "final_state": {
            "initial_inventory": context.get('init_inventory'),
            "available_inventory": context.get('final_available'),
            "confirmed_orders": context.get('confirmed_orders_metric')
        },
        "idempotency": {
            "status": context.get('idempotency_pass')
        }
    }
    
    with open(json_path, 'w') as f:
        json.dump(data, f, indent=4)
        
    # Save TXT
    with open(txt_path, 'w') as f:
        f.write("========================================\n")
        f.write("FLASHGUARD FLASH-SALE TEST RESULTS\n")
        f.write("========================================\n\n")
        f.write(f"Product ID:             {args.product_id}\n")
        f.write(f"Initial Inventory:      {context.get('init_inventory')}\n")
        f.write(f"Total Buyers:           {context['total_buyers']}\n\n")
        
        f.write("--- RESERVATION PHASE ---\n")
        f.write(f"Reservation Requests:   {context['total_buyers']}\n")
        f.write(f"Successful:             {context['success_count']}\n")
        f.write(f"Rejected:               {context['reject_count']}\n")
        f.write(f"HTTP Errors:            {context['error_count']}\n")
        f.write(f"Average Latency:        {context.get('avg_latency', 0):.2f} ms\n")
        f.write(f"P95 Latency:            {context.get('p95_latency', 0):.2f} ms\n\n")
        
        f.write("--- INVENTORY CHECK ---\n")
        f.write(f"Available Inventory:    {context.get('current_available')}\n")
        f.write(f"Oversell Detected:      {context.get('oversold')}\n")
        f.write(f"Negative Inventory:     {context.get('negative_inv')}\n")
        f.write(f"Inventory Invariant:    {'PASS' if context.get('invariant_pass') else 'FAIL'}\n\n")
        
        f.write("--- PAYMENT PHASE ---\n")
        f.write(f"Orders to Pay:          {len(context.get('orders_to_pay', []))}\n")
        f.write(f"Payments Successful:    {context.get('payments_successful', 0)}\n")
        f.write(f"Payments Failed:        {context.get('payments_failed', 0)}\n")
        f.write(f"Confirmed Orders:       {context.get('payments_successful', 0)}\n\n")
        
        f.write("--- FINAL INVENTORY ---\n")
        f.write(f"Initial:                {context.get('init_inventory')}\n")
        f.write(f"Available:              {context.get('final_available')}\n")
        f.write(f"Confirmed:              {context.get('confirmed_orders_metric')}\n\n")
        
        f.write("--- IDEMPOTENCY ---\n")
        f.write(f"Duplicate Request:      {context.get('idempotency_pass')}\n")
        f.write(f"Duplicate Order:        {context.get('dup_order')}\n")
        f.write(f"Extra Inventory Deducted: {context.get('extra_deduct')}\n\n")
        
    print(f"\nResults saved to:")
    print(f" - {json_path}")
    print(f" - {txt_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FlashGuard end-to-end flash-sale load testing tool")
    parser.add_argument("--product-id", type=int, default=6, help="Product ID to run the test against")
    parser.add_argument("--base-url", type=str, default="http://localhost:8000", help="Base URL of the FastAPI application")
    parser.add_argument("--csv-file", type=str, default="flashguard_200_buyers_product6.csv", help="CSV file containing test users")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(run_test(args))
    except KeyboardInterrupt:
        print("\nTest cancelled by user.")
        sys.exit(0)
