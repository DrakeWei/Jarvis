from discounts import loyalty_discount


def total_after_discount(subtotal: int, loyalty_points: int) -> int:
    return subtotal - loyalty_discount(subtotal, loyalty_points)
