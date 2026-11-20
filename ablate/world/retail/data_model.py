# Adapted from sierra-research/tau2-bench
#   source repo: https://github.com/sierra-research/tau2-bench
#   file:        src/tau2/domains/retail/data_model.py
#   commit:      c06b07dd7c4d4ec61ba6ff9bcdf0b15dd7ef36d4
#   licence:     MIT, Copyright (c) 2025 Sierra Research (see THIRD_PARTY.md)
# Local changes: removed the tau2 `DB` base class and RETAIL_DB_PATH; RetailDB
# is a plain pydantic model loaded from Ablate's small seed_db.json. Dropped
# the unused BaseOrder duplicate and get_statistics().
"""Pydantic models for the retail world."""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class Variant(BaseModel):
    item_id: str
    options: Dict[str, str]
    available: bool
    price: float


class Product(BaseModel):
    name: str
    product_id: str
    variants: Dict[str, Variant]


class UserName(BaseModel):
    first_name: str
    last_name: str


class UserAddress(BaseModel):
    address1: str
    address2: str
    city: str
    country: str
    state: str
    zip: str


class PaymentMethodBase(BaseModel):
    source: str
    id: str


class CreditCard(PaymentMethodBase):
    source: Literal["credit_card"]
    brand: str
    last_four: str


class Paypal(PaymentMethodBase):
    source: Literal["paypal"]


class GiftCard(PaymentMethodBase):
    source: Literal["gift_card"]
    balance: float


PaymentMethod = Union[CreditCard, GiftCard, Paypal]


class User(BaseModel):
    user_id: str
    name: UserName
    address: UserAddress
    email: str
    payment_methods: Dict[str, PaymentMethod]
    orders: List[str]


class OrderFullfilment(BaseModel):
    tracking_id: list[str]
    item_ids: list[str]


class OrderItem(BaseModel):
    name: str
    product_id: str
    item_id: str
    price: float
    options: Dict[str, str]


OrderPaymentType = Literal["payment", "refund"]


class OrderPayment(BaseModel):
    transaction_type: OrderPaymentType
    amount: float
    payment_method_id: str


OrderStatus = Literal[
    "processed",
    "pending",
    "pending (item modified)",
    "delivered",
    "cancelled",
    "exchange requested",
    "return requested",
]

CancelReason = Literal["no longer needed", "ordered by mistake"]


class Order(BaseModel):
    order_id: str
    user_id: str
    address: UserAddress
    items: List[OrderItem]
    status: OrderStatus
    fulfillments: List[OrderFullfilment]
    payment_history: List[OrderPayment]
    cancel_reason: Optional[CancelReason] = None
    exchange_items: Optional[List[str]] = None
    exchange_new_items: Optional[List[str]] = None
    exchange_payment_method_id: Optional[str] = None
    exchange_price_difference: Optional[float] = None
    return_items: Optional[List[str]] = None
    return_payment_method_id: Optional[str] = None


class RetailDB(BaseModel):
    products: Dict[str, Product] = Field(default_factory=dict)
    users: Dict[str, User] = Field(default_factory=dict)
    orders: Dict[str, Order] = Field(default_factory=dict)
