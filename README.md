# FlashGuard — Distributed Flash-Sale Inventory & Order System

## 1. What FlashGuard is
FlashGuard is a technically correct distributed flash-sale inventory and order system. It is designed to handle extremely high concurrency scenarios where thousands of users attempt to purchase a very limited number of items simultaneously.

## 2. The Problem Being Solved
When a popular item goes on flash sale (e.g., 100 smartphones, 10,000 users), standard e-commerce implementations fail because they suffer from race conditions. This often results in overselling (selling more units than actually exist in inventory) or database deadlocks.

FlashGuard solves this by decoupling high-speed inventory reservation from durable order processing, guaranteeing that inventory never becomes negative.

## 3. Architecture
The system uses a React frontend, a FastAPI backend, Redis for atomic high-speed inventory reservation, PostgreSQL for durable source of truth, and a background worker for timeout recovery. See `docs/architecture.md` for a detailed breakdown.

## 4. Technology Stack
- **Frontend**: React, Vite, Tailwind CSS, Axios
- **Backend**: Python 3.11, FastAPI, SQLAlchemy, Pydantic
- **Database**: PostgreSQL
- **Cache & Concurrency**: Redis (Lua Scripts)
- **Infrastructure**: Docker & Docker Compose

## 5. Database Model
- **Product**: Details about the flash sale item, including total and available inventory.
- **Reservation**: Tracks the temporary holding of inventory with an idempotency key and expiration timestamp.
- **Order**: Created alongside a reservation, tracks the final purchase state and total amount.
- **Payment**: Tracks the outcome of the payment simulation.

## 6. Reservation Flow
1. User requests to buy a product, sending an idempotency key.
2. Backend validates product existence.
3. Backend executes a Lua script in Redis to atomically check inventory, check idempotency, and decrement inventory.
4. If successful, durable records (Reservation & Order) are created in PostgreSQL.

## 7. State Machine
**Order States:**
- `PAYMENT_PENDING` -> `CONFIRMED`
- `PAYMENT_PENDING` -> `PAYMENT_FAILED` (Releases Inventory)
- `PAYMENT_PENDING` -> `PAYMENT_TIMEOUT`
- `PAYMENT_PENDING` -> `CANCELLED`

**Reservation States:**
- `RESERVED` -> `CONFIRMED`
- `RESERVED` -> `EXPIRED` (Worker releases inventory)
- `RESERVED` -> `CANCELLED`

## 8. Idempotency Strategy
The client sends an `idempotency_key` with every purchase request. Redis checks if this key exists using the Lua script. If it does, the backend fetches the existing order from PostgreSQL rather than consuming more inventory.

## 9. Redis's Role
Redis is purely used for the hot path: atomic inventory decrements using Lua scripts. It prevents race conditions during the initial burst of traffic.

## 10. PostgreSQL's Role
PostgreSQL is the durable source of truth. It stores all final states. If Redis data is lost, PostgreSQL retains the actual orders and remaining inventory can be re-calculated or synced.

## 11. Payment Simulation
A simulated payment endpoint (`/api/orders/{order_id}/pay`) allows simulating `SUCCESS`, `FAILED`, and `TIMEOUT` scenarios to observe how the system handles state transitions and inventory recovery.

## 12. Reservation Expiration
A background worker continuously scans PostgreSQL for `RESERVED` reservations where `expires_at` has passed. It changes their status to `EXPIRED` and atomically restores the inventory both in PostgreSQL and Redis.

## 13. How to Run Locally
Run the entire stack using Docker Compose:
```bash
docker compose up --build
```
- Frontend: http://localhost:5173
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

**Steps to start:**
1. Run `docker compose up --build -d`
2. Open the Frontend.
3. Go to Admin Dashboard (top right) and click **Seed / Reset Data**.
4. Go back to the Store and try purchasing.

## 14. Environment Variables
See `.env.example`:
- `DATABASE_URL`: PostgreSQL connection string.
- `REDIS_URL`: Redis connection string.
- `VITE_API_URL`: Backend API URL for the frontend.

## 15. API Endpoints
- `GET /api/products`
- `GET /api/products/{id}`
- `GET /api/products/{id}/inventory`
- `POST /api/orders/reserve`
- `POST /api/orders/{id}/pay`
- `GET /api/orders/{id}`
- `POST /api/orders/{id}/cancel`
- `GET /api/reservations/{id}`
- `GET /api/admin/metrics`
- `POST /api/admin/seed`
- `GET /api/health`
