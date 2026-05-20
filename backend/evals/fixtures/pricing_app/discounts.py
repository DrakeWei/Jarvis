def loyalty_discount(subtotal: int, loyalty_points: int) -> int:
    if loyalty_points >= 100:
        return subtotal // 20
    return 0
