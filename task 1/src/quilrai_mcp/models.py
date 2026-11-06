"""Strict tool schemas; the same models generate discovery schemas and validate calls."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CustomerID = Annotated[
    str, StringConstraints(pattern=r"^CUST-[0-9]{5}$", min_length=10, max_length=10)
]


class CustomerInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    customer_id: CustomerID = Field(description="Customer ID: CUST- followed by five ASCII digits")


class RefundInput(CustomerInput):
    amount: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    reason: Annotated[str, StringConstraints(min_length=10)]
