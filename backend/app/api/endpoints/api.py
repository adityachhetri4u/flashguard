from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.db.database import get_db
from app.models.models import Product, Order, Reservation, Payment, ReservationStatus, OrderStatus, PaymentStatus
from app.schemas.schemas import (
    ProductResponse, ReserveRequest, ReservationResponse, 
    PaymentRequest, PaymentResponse, OrderResponse
)
from app.services.reservation import reserve_inventory, process_payment, cancel_order
from app.core.redis import redis_client

router = APIRouter()

@router.get("/health")
def health_check():
    return {"status": "HEALTHY"}

@router.get("/products", response_model=List[ProductResponse])
def get_products(db: Session = Depends(get_db)):
    products = db.query(Product).all()
    return products

@router.get("/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    return product

@router.get("/products/{product_id}/inventory")
def get_product_inventory(product_id: int, db: Session = Depends(get_db)):
    # Prefer redis for speed
    inventory_key = f"product:{product_id}:inventory"
    redis_inv = redis_client.get(inventory_key)
    
    if redis_inv is not None:
        return {"product_id": product_id, "available_inventory": int(redis_inv)}
    
    product = db.query(Product).filter(Product.id == product_id).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
        
    return {"product_id": product_id, "available_inventory": product.available_inventory}

@router.post("/orders/reserve", response_model=ReservationResponse)
def reserve_order(request: ReserveRequest, db: Session = Depends(get_db)):
    return reserve_inventory(db, request)

@router.post("/orders/{order_id}/pay", response_model=PaymentResponse)
def pay_order(order_id: str, request: PaymentRequest, db: Session = Depends(get_db)):
    return process_payment(db, order_id, request)

@router.get("/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: str, db: Session = Depends(get_db)):
    order = db.query(Order).filter(Order.id == order_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    return order

@router.post("/orders/{order_id}/cancel", response_model=OrderResponse)
def cancel_order_endpoint(order_id: str, db: Session = Depends(get_db)):
    return cancel_order(db, order_id)

@router.get("/reservations/{reservation_id}", response_model=ReservationResponse)
def get_reservation(reservation_id: str, db: Session = Depends(get_db)):
    reservation = db.query(Reservation).filter(Reservation.id == reservation_id).first()
    if not reservation:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return reservation

@router.post("/admin/seed")
def seed_data(db: Session = Depends(get_db)):
    # Clear existing data
    db.query(Payment).delete()
    db.query(Order).delete()
    db.query(Reservation).delete()
    db.query(Product).delete()
    redis_client.flushdb()
    
    # Create product
    product = Product(
        name="Premium Smartphone",
        description="Limited edition flash sale smartphone.",
        price=49999.0,
        total_inventory=100,
        available_inventory=100
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    
    # Sync redis
    redis_client.set(f"product:{product.id}:inventory", 100)
    
    return {"status": "success", "message": "Database seeded.", "product_id": product.id}

@router.get("/admin/metrics")
def get_metrics(db: Session = Depends(get_db)):
    product = db.query(Product).first()
    if not product:
        return {"error": "No product configured"}
        
    total_inv = product.total_inventory
    available_inv = product.available_inventory
    reserved_inv = db.query(Reservation).filter(Reservation.status == ReservationStatus.RESERVED).count() # Approximating 1 qty per res
    
    confirmed_orders = db.query(Order).filter(Order.status == OrderStatus.CONFIRMED).count()
    failed_orders = db.query(Order).filter(Order.status == OrderStatus.PAYMENT_FAILED).count()
    expired_reservations = db.query(Reservation).filter(Reservation.status == ReservationStatus.EXPIRED).count()
    active_reservations = db.query(Reservation).filter(Reservation.status == ReservationStatus.RESERVED).count()
    
    total_purchase_attempts = db.query(Reservation).count()
    payment_failures = db.query(Payment).filter(Payment.status == PaymentStatus.FAILED).count()
    
    return {
        "initial_inventory": total_inv,
        "available_inventory": available_inv,
        "reserved_inventory": reserved_inv,
        "confirmed_orders": confirmed_orders,
        "failed_orders": failed_orders,
        "expired_reservations": expired_reservations,
        "active_reservations": active_reservations,
        "total_purchase_attempts": total_purchase_attempts,
        "payment_failures": payment_failures,
        "system": {
            "api": "HEALTHY",
            "redis": "HEALTHY" if redis_client.ping() else "DOWN",
            "postgresql": "HEALTHY"
        }
    }
