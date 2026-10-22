"""All financial calculations use integer kopecks and explicit half-up rounding."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

MAX_PRICE_CENTS = 100_000_000_00


def parse_price(value: str) -> int:
    try:
        amount = Decimal(value.strip().replace(",", "."))
        if (
            not amount.is_finite()
            or amount <= 0
            or amount > Decimal(MAX_PRICE_CENTS) / 100
        ):
            raise ValueError("Цена вне допустимого диапазона.")
        if amount != amount.quantize(Decimal("0.01")):
            raise ValueError("Укажите не более двух знаков после запятой.")
        return int(amount * 100)
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError("Введите сумму от 0.01 до 100000000 рублей.") from exc


def portion(cents: int, ratio: str) -> int:
    return int(
        (Decimal(cents) * Decimal(ratio)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def amounts(cents: int) -> dict:
    total = cents + portion(cents, "0.13")
    buyer_deposit = portion(total, "0.25")
    return dict(
        base=cents,
        total=total,
        fee=total - cents,
        buyer_deposit=buyer_deposit,
        seller_deposit=portion(total, "0.05"),
        remainder=total - buyer_deposit,
    )


def rubles(cents: int) -> str:
    return f"{Decimal(cents) / 100:.2f}"
