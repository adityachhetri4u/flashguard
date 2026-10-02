import pytest
import uuid
import time
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.db.database import Base
from app.models.models import Product, Reservation, Order, ReservationStatus, OrderStatus, PaymentStatus
from app.schemas.schemas import ReserveRequest, PaymentRequest
from app.services.reservation import reserve_inventory, cancel_order, process_payment
from app.workers.background_worker import expire_reservations
from app.core.redis import redis_client

# Test Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture(scope="function")
def db():
    Base.metadata.create_all(bind=engine)
    redis_client.flushdb()
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

def seed_product(db, inventory=100):
    product = Product(name="Test Phone", price=1000.0, total_inventory=inventory, available_inventory=inventory)
    db.add(product)
    db.commit()
    db.refresh(product)
    redis_client.set(f"product:{product.id}:inventory", inventory)
    return product

def test_1_single_reservation(db):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    reserve_inventory(db, req)
    
    db.refresh(product)
    assert product.available_inventory == 99

def test_2_reservation_expires(db, monkeypatch):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    db.refresh(product)
    assert product.available_inventory == 99
    
    # Mock datetime to force expiration
    import datetime
    from app.workers import background_worker
    
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + datetime.timedelta(seconds=120)
            
    monkeypatch.setattr(background_worker, 'datetime', MockDatetime)
    
    # Needs a monkeypatch of db session for worker to use our test DB
    monkeypatch.setattr(background_worker, 'SessionLocal', lambda: db)
    
    expire_reservations()
    
    db.refresh(product)
    assert product.available_inventory == 100
    
    res = db.query(Reservation).first()
    assert res.status == ReservationStatus.EXPIRED

def test_3_run_expiry_twice(db, monkeypatch):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    import datetime
    from app.workers import background_worker
    class MockDatetime(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + datetime.timedelta(seconds=120)
    monkeypatch.setattr(background_worker, 'datetime', MockDatetime)
    monkeypatch.setattr(background_worker, 'SessionLocal', lambda: db)
    
    expire_reservations()
    db.refresh(product)
    assert product.available_inventory == 100
    
    # Run again
    expire_reservations()
    db.refresh(product)
    assert product.available_inventory == 100 # NOT 101

def test_4_cancel_reservation(db):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    cancel_order(db, res.order_id)
    db.refresh(product)
    assert product.available_inventory == 100

def test_5_cancel_reservation_twice(db):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    cancel_order(db, res.order_id)
    db.refresh(product)
    assert product.available_inventory == 100
    
    # Second cancel should fail due to invalid state transition
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        cancel_order(db, res.order_id)
        
    db.refresh(product)
    assert product.available_inventory == 100

def test_6_idempotency(db):
    product = seed_product(db, 100)
    idemp_key = str(uuid.uuid4())
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=idemp_key)
    
    reserve_inventory(db, req)
    reserve_inventory(db, req)
    reserve_inventory(db, req)
    
    db.refresh(product)
    assert product.available_inventory == 99
    
    count = db.query(Reservation).count()
    assert count == 1

def test_9_successful_payment(db):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    pay_req = PaymentRequest(simulate_status=PaymentStatus.SUCCESS)
    process_payment(db, res.order_id, pay_req)
    
    db.refresh(product)
    assert product.available_inventory == 99 # Not restored
    
    order = db.query(Order).first()
    assert order.status == OrderStatus.CONFIRMED

def test_10_failed_payment(db):
    product = seed_product(db, 100)
    req = ReserveRequest(user_id="user1", product_id=product.id, idempotency_key=str(uuid.uuid4()))
    res = reserve_inventory(db, req)
    
    pay_req = PaymentRequest(simulate_status=PaymentStatus.FAILED)
    process_payment(db, res.order_id, pay_req)
    
    db.refresh(product)
    assert product.available_inventory == 100 # Restored Exactly once
