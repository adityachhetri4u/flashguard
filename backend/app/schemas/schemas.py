from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from app.models.models import ReservationStatus, OrderStatus, PaymentStatus

class ProductBase(BaseModel):
    name: str
    description: str
    price: float
    total_inventory: int

class ProductCreate(ProductBase):
    pass

class ProductResponse(ProductBase):
    id: int
    available_inventory: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class ReserveRequest(BaseModel):
    user_id: str
    product_id: int
    quantity: int = 1
    idempotency_key: str

class ReservationResponse(BaseModel):
    id: str
    user_id: str
    product_id: int
    quantity: int
    status: ReservationStatus
    expires_at: Optional[datetime] = None
    order_id: Optional[str] = None # Added field

    model_config = ConfigDict(from_attributes=True)

class PaymentRequest(BaseModel):
    simulate_status: PaymentStatus = PaymentStatus.SUCCESS

class OrderResponse(BaseModel):
    id: str
    user_id: str
    product_id: int
    reservation_id: str
    quantity: int
    total_amount: float
    status: OrderStatus
    created_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

class PaymentResponse(BaseModel):
    id: str
    order_id: str
    amount: float
    status: PaymentStatus

    model_config = ConfigDict(from_attributes=True)
