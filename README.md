# FlashGuard: Distributed Flash-Sale System

![FlashGuard](https://img.shields.io/badge/Status-Completed-brightgreen) ![Concurrency](https://img.shields.io/badge/Concurrency-10k_Users-blue) ![Stack](https://img.shields.io/badge/Stack-FastAPI%20%7C%20Redis%20%7C%20PostgreSQL-blueviolet)

FlashGuard is a highly concurrent, distributed e-commerce ordering system built to handle extreme traffic spikes—such as selling 100 smartphones to 10,000 eager buyers within seconds. 

The system guarantees **zero overselling**, handles **partial failures**, guarantees **idempotency**, and ensures strict **inventory consistency** even under extreme DDoS-level request bursts.

## 🚀 Quick Start (How to Run)

The entire application (Frontend, Backend, Redis, PostgreSQL, and Background Worker) is containerized.

**Prerequisites:** Docker and Docker Compose.

1. **Clone the repository**
   ```bash
   git clone https://github.com/adityachhetri4u/flashguard.git
   cd flashguard
   ```

2. **Start the system**
   ```bash
   docker-compose up -d --build
   ```

3. **Access the Application**
   - **Frontend UI:** `http://localhost:5173`
   - **Backend API Docs:** `http://localhost:8000/docs`
   - **Admin Metrics API:** `http://localhost:8000/api/admin/metrics`

4. **Run the Load Test (Optional)**
   You can simulate a 10,000-user flash sale using the provided Python scripts in the `load_tests/` directory.
   ```bash
   pip install -r load_tests/requirements.txt
   python load_tests/flash_sale_200.py --csv-file load_tests/flashguard_10000_buyers_product16.csv --product-id 16
   ```

---

## 🧠 The Problem & Domain

In a flash sale, thousands of users attempt to purchase a highly limited item at the exact same millisecond. Traditional database setups fail here due to:
- **Database Lock Contention:** 10,000 threads trying to acquire a row lock on a single inventory record will crash the database or timeout.
- **Race Conditions (Overselling):** If read and write operations aren't perfectly atomic, the system will sell units that don't exist.
- **Lost Updates & Retries:** Users experiencing network lag will click "Buy" multiple times, resulting in duplicate charges or duplicate inventory deductions.

**The Absolute Business Rule:** The system must *never* sell more units than actually exist.

---

## 🏗️ Solution Architecture & Approach

To solve this, FlashGuard decouples the initial traffic burst from the durable database using an **In-Memory Gatekeeper pattern**.

### 1. The Gatekeeper (Redis)
All incoming purchase requests first hit a single-threaded Redis instance running an atomic **Lua Script**. 
- The script checks idempotency (has this user already requested this?).
- It checks if `inventory > 0`.
- If valid, it decrements the inventory entirely in memory and returns success.
- **Result:** Over 9,900 excess requests are instantly rejected (`400 OUT_OF_STOCK`) in microseconds without ever touching PostgreSQL.

### 2. Durable Persistence (PostgreSQL)
Only the successfully admitted requests (the 100 winners) proceed to PostgreSQL. 
- The backend acquires a safe row lock, deducts the inventory atomically (`available_inventory >= request.quantity`), and writes the `Reservation` and `Order`. 
- Even if Redis goes out of sync, PostgreSQL's strict SQL constraints act as the ultimate source of truth, making overselling mathematically impossible.

### 3. Background Expiry Worker
A background Python daemon constantly polls for `Reservation` records that have expired (e.g., users who reserved but abandoned the checkout). It atomically cancels the order and restores the inventory to both PostgreSQL and Redis.

---

## 🔄 State Machine & Transitions

The lifecycle of an order rigorously follows this state machine:

`AVAILABLE` ➔ `RESERVED` ➔ `PAYMENT_PENDING` ➔ `CONFIRMED`

| State | Trigger | Action | Failure Handling |
|-------|---------|--------|------------------|
| **AVAILABLE** | User clicks "Buy" | Redis Lua atomic decrement. | If Redis fails/restarts, initializes via `setnx` from DB. |
| **RESERVED** | Redis approves | Insert DB Reservation. | If DB insert fails, TTL expires in Redis automatically. |
| **PAYMENT_PENDING** | DB Flush succeeds | Insert DB Order. | If user drops off, Background Worker expires after 10 mins. |
| **CONFIRMED** | Payment success | Update DB statuses. | Idempotency key prevents duplicate payment processing. |

---

## 🛡️ Handling Edge Cases & Failures

- **Thousands of concurrent requests:** Handled by increasing the `uvicorn` and PostgreSQL connection pools (`pool_size=250`) and using Redis to absorb 90% of the read/write load.
- **Duplicate client requests (Retries):** The Redis Lua script stores an `idempotency_key` upon successful reservation. If the same request arrives again, the script returns `-1`, and the API fetches the existing reservation instead of deducting new inventory.
- **Service Crashes:** If the API crashes after Redis deducts inventory but before PostgreSQL writes the reservation, the Redis idempotency key will eventually expire (TTL), or the Background Worker will reconcile the state.
- **Redis Initialization Race Condition:** If Redis restarts, the first thousands of requests could theoretically reset the inventory multiple times. This is mitigated by using the atomic `SETNX` command to load inventory from PostgreSQL only if the key strictly does not exist.

---

## 🔮 Limitations & Future Improvements

While this system handles 10,000 concurrent requests smoothly, further scaling would require:

1. **Message Queues (Kafka/RabbitMQ):** Instead of FastAPI synchronously writing to PostgreSQL after Redis approval, it should push the valid reservation to a message queue. This would return a response to the user instantly ("You're in line!") and workers would insert into PostgreSQL at their own pace, preventing DB connection pool exhaustion entirely.
2. **Redis Cluster:** The current setup relies on a single Redis node. For higher availability, a Redis Cluster or Sentinel setup should be used to prevent a single point of failure.
3. **Multiple Warehouses:** Currently, inventory is pooled into one counter. To support multiple warehouses, the Lua script could dynamically route checks to specific warehouse keys based on user geography.
