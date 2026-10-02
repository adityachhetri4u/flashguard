import time
import logging
from datetime import datetime, timezone
from app.db.database import SessionLocal
from app.models.models import Reservation, Order, Product, ReservationStatus, OrderStatus
from app.core.redis import redis_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def expire_reservations():
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        
        # Find reservations that are expired and still RESERVED
        expired_reservations = db.query(Reservation).filter(
            Reservation.status == ReservationStatus.RESERVED,
            Reservation.expires_at <= now
        ).all()
        
        for res in expired_reservations:
            # Atomic state transition
            updated_res = db.query(Reservation).filter(
                Reservation.id == res.id,
                Reservation.status == ReservationStatus.RESERVED
            ).update({"status": ReservationStatus.EXPIRED}, synchronize_session=False)
            
            if updated_res:
                # Cancel related order if pending
                db.query(Order).filter(
                    Order.reservation_id == res.id,
                    Order.status == OrderStatus.PAYMENT_PENDING
                ).update({"status": OrderStatus.CANCELLED}, synchronize_session=False)
                
                # Return inventory atomically
                db.query(Product).filter(Product.id == res.product_id).update({
                    "available_inventory": Product.available_inventory + res.quantity
                }, synchronize_session=False)
                
                redis_client.incrby(f"product:{res.product_id}:inventory", res.quantity)
                
                logger.info(f"Expired reservation {res.id}, released inventory.")
                
            db.commit()
            
    except Exception as e:
        logger.error(f"Error in background worker: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    logger.info("Starting background worker for reservation expiration...")
    while True:
        expire_reservations()
        time.sleep(5)
