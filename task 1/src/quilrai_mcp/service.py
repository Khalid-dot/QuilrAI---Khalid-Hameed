"""In-memory demonstration backend. No payments are executed or persisted."""

from uuid import uuid4

from quilrai_mcp.models import CustomerInput, RefundInput

CUSTOMERS = {
    "CUST-00001": {"customer_id": "CUST-00001", "name": "Alex Morgan", "status": "active"},
    "CUST-00002": {"customer_id": "CUST-00002", "name": "Sam Taylor", "status": "active"},
}


class CustomerNotFound(Exception):
    """Valid identifier without a matching mock record."""


def get_customer_record(arguments: CustomerInput) -> dict:
    record = CUSTOMERS.get(arguments.customer_id)
    if record is None:
        raise CustomerNotFound("Customer record not found")
    return dict(record)


def trigger_refund(arguments: RefundInput) -> dict:
    get_customer_record(arguments)
    return {
        "refund_id": f"REF-{uuid4().hex}",
        "customer_id": arguments.customer_id,
        "amount": arguments.amount,
        "reason": arguments.reason,
        "status": "simulated",
    }
