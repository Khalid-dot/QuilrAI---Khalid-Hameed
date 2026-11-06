import math

import pytest
from pydantic import ValidationError

from quilrai_mcp.models import CustomerInput, RefundInput


@pytest.mark.parametrize(
    "value",
    [
        "cust-00001",
        "CUST-1234",
        "CUST-123456",
        "CUST-ABCDE",
        " CUST-00001",
        "CUST-00001\n",
        "CUST-１２３４５",
        12345,
        None,
        True,
        "",
    ],
)
def test_invalid_customer_ids(value):
    with pytest.raises(ValidationError):
        CustomerInput(customer_id=value)


@pytest.mark.parametrize("value", [0, -1.5, "12.5", True, None, math.inf, -math.inf, math.nan])
def test_invalid_amounts(value):
    with pytest.raises(ValidationError):
        RefundInput(customer_id="CUST-00001", amount=value, reason="Duplicate payment")


@pytest.mark.parametrize("value", ["", "123456789", None, 1234567890, False])
def test_invalid_reasons(value):
    with pytest.raises(ValidationError):
        RefundInput(customer_id="CUST-00001", amount=1.5, reason=value)


def test_boundaries_and_json_numbers():
    for amount in [1, 0.01, 12.5]:
        model = RefundInput(customer_id="CUST-00000", amount=amount, reason="1234567890")
        assert model.amount == amount


def test_missing_and_extra_fields():
    for value in [{}, {"customer_id": "CUST-00001", "unexpected": True}]:
        with pytest.raises(ValidationError):
            CustomerInput.model_validate(value)


def test_schema_is_strict_and_discoverable():
    schema = RefundInput.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"customer_id", "amount", "reason"}
    assert schema["properties"]["amount"]["exclusiveMinimum"] == 0
    assert schema["properties"]["reason"]["minLength"] == 10
