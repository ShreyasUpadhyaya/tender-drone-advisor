from decimal import Decimal, InvalidOperation

from app.solver.contracts import MAX_INTEGER


class SolverError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def checked(value: int) -> int:
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise SolverError("integer_overflow_or_negative")
    return value


def scaled(value: object, scale: int = 1) -> int:
    if type(value) not in (int, float):
        raise SolverError("invalid_numeric_specification")
    try:
        number = Decimal(str(value)) * scale
        if not number.is_finite() or number != number.to_integral_value():
            raise SolverError("unsupported_measurement_precision")
        return checked(int(number))
    except InvalidOperation:
        raise SolverError("invalid_numeric_specification") from None


def bps(value: int, rate: int) -> int:
    return checked((checked(value) * rate + 9999) // 10000)


def derate(value: int, margin: int) -> int:
    return checked(value * (10000 - margin) // 10000)
