import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException
from app.models.models import Product, Reservation, Order, Payment, ReservationStatus, OrderStatus, PaymentStatus
from app.schemas.schemas import ReserveRequest, PaymentRequest
from app.core.redis import redis_client, reserve_lua
from app.core.config import settings

def reserve_inventory(db: Session, request: ReserveRequest) -> Reservation:
    # 1. Check idempotency in Postgres (fallback if Redis expired/restarted)
    existing_reservation = db.query(Reservation).filter(
        Reservation.idempotency_key == request.idempotency_key
    ).first()
    
    if existing_reservation:
        order = db.query(Order).filter(Order.reservation_id == existing_reservation.id).first()
        if order:
            existing_reservation.order_id = order.id
        return existing_reservation

    # 2. Check product in Postgres
    product = db.query(Product).filter(Product.id == request.product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Sync Redis inventory if not present (only first time)
    inventory_key = f"product:{request.product_id}:inventory"
    if redis_client.get(inventory_key) is None:
        redis_client.set(inventory_key, product.available_inventory)

    # 3. Perform atomic reservation in Redis
    reservation_id = str(uuid.uuid4())
    idempotency_key = f"idempotency:{request.idempotency_key}"
    
    result = reserve_lua(keys=[inventory_key, idempotency_key], args=[request.quantity, reservation_id])
    
    if result == -1:
        # Idempotency hit in Redis, fetch from DB
        # This shouldn't normally happen since we checked DB first, but could happen under heavy concurrency
        res = db.query(Reservation).filter(Reservation.idempotency_key == request.idempotency_key).first()
        if res:
            order = db.query(Order).filter(Order.reservation_id == res.id).first()
            if order:
                res.order_id = order.id
            return res
        raise HTTPException(status_code=400, detail="DUPLICATE_REQUEST")
    elif result == 0:
        raise HTTPException(status_code=400, detail="OUT_OF_STOCK")
    
    # 4. Create durable state in PostgreSQL
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.RESERVATION_TIMEOUT_SECONDS)
    
    reservation = Reservation(
        id=reservation_id,
        user_id=request.user_id,
        product_id=request.product_id,
        quantity=request.quantity,
        status=ReservationStatus.RESERVED,
        idempotency_key=request.idempotency_key,
        expires_at=expires_at
    )
    
    # Decrease inventory in DB atomically
    updated = db.query(Product).filter(
        Product.id == request.product_id,
        Product.available_inventory >= request.quantity
    ).update({
        "available_inventory": Product.available_inventory - request.quantity
    })
    
    if updated == 0:
        # In case DB is out of sync with Redis
        raise HTTPException(status_code=400, detail="OUT_OF_STOCK")
        
    db.add(reservation)
    db.flush() # Ensure reservation is inserted before order
    
    # Create an order
    order_id = str(uuid.uuid4())
    order = Order(
        id=order_id,
        user_id=request.user_id,
        product_id=request.product_id,
        reservation_id=reservation.id, # Use reservation.id directly
        quantity=request.quantity,
        total_amount=product.price * request.quantity,
        status=OrderStatus.PAYMENT_PENDING
    )
    
    db.add(order)
    db.commit()
    db.refresh(reservation)
    
    reservation.order_id = order.id
    
    return reservation

def process_payment(db: Session, order_id: str, request: PaymentRequest) -> Payment:
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.status == OrderStatus.PAYMENT_PENDING
    ).first()
    
    if not order:
        raise HTTPException(status_code=400, detail="INVALID_STATE_TRANSITION")
        
    reservation = db.query(Reservation).filter(Reservation.id == order.reservation_id).first()
    
    payment_id = str(uuid.uuid4())
    payment = Payment(
        id=payment_id,
        order_id=order.id,
        payment_reference=f"SIM_{payment_id[:8]}",
        amount=order.total_amount,
        status=request.simulate_status
    )
    db.add(payment)
    
    # State transition based on simulated payment
    if request.simulate_status == PaymentStatus.SUCCESS:
        updated = db.query(Order).filter(Order.id == order.id, Order.status == OrderStatus.PAYMENT_PENDING).update({"status": OrderStatus.CONFIRMED})
        if updated:
            db.query(Reservation).filter(Reservation.id == reservation.id, Reservation.status == ReservationStatus.RESERVED).update({"status": ReservationStatus.CONFIRMED})
    elif request.simulate_status in [PaymentStatus.FAILED, PaymentStatus.TIMEOUT]:
        new_status = OrderStatus.PAYMENT_FAILED if request.simulate_status == PaymentStatus.FAILED else OrderStatus.PAYMENT_TIMEOUT
        updated_order = db.query(Order).filter(Order.id == order.id, Order.status == OrderStatus.PAYMENT_PENDING).update({"status": new_status})
        
        if updated_order:
            updated_res = db.query(Reservation).filter(Reservation.id == reservation.id, Reservation.status == ReservationStatus.RESERVED).update({"status": ReservationStatus.CANCELLED})
            if updated_res:
                # Return inventory atomically
                db.query(Product).filter(Product.id == order.product_id).update({
                    "available_inventory": Product.available_inventory + order.quantity
                })
                redis_client.incrby(f"product:{order.product_id}:inventory", order.quantity)
        
    db.commit()
    db.refresh(payment)
    return payment

def cancel_order(db: Session, order_id: str):
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.status.in_([OrderStatus.PAYMENT_PENDING, OrderStatus.PAYMENT_TIMEOUT])
    ).first()
    
    if not order:
        raise HTTPException(status_code=400, detail="INVALID_STATE_TRANSITION")
        
    updated_order = db.query(Order).filter(
        Order.id == order_id, 
        Order.status.in_([OrderStatus.PAYMENT_PENDING, OrderStatus.PAYMENT_TIMEOUT])
    ).update({"status": OrderStatus.CANCELLED}, synchronize_session=False)
    
    if updated_order:
        updated_res = db.query(Reservation).filter(
            Reservation.id == order.reservation_id, 
            Reservation.status == ReservationStatus.RESERVED
        ).update({"status": ReservationStatus.CANCELLED}, synchronize_session=False)
        
        if updated_res:
            # Return inventory atomically
            db.query(Product).filter(Product.id == order.product_id).update({
                "available_inventory": Product.available_inventory + order.quantity
            }, synchronize_session=False)
            redis_client.incrby(f"product:{order.product_id}:inventory", order.quantity)
    
    db.commit()
    return db.query(Order).filter(Order.id == order_id).first()
