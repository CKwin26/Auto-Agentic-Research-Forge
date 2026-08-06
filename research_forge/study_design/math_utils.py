"""Small dependency-free statistical functions used by certified designs."""

from __future__ import annotations

import math
from statistics import NormalDist


def _continued_beta(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1e-300 if abs(d) < 1e-300 else d
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1e-300 if abs(d) < 1e-300 else d
        c = 1.0 + aa / c
        c = 1e-300 if abs(c) < 1e-300 else c
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _continued_beta(a, b, x) / a
    return 1.0 - front * _continued_beta(b, a, 1.0 - x) / b


def student_t_cdf(value: float, degrees_of_freedom: float) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("degrees_of_freedom must be positive")
    if value == 0:
        return 0.5
    x = degrees_of_freedom / (degrees_of_freedom + value * value)
    tail = 0.5 * regularized_beta(x, degrees_of_freedom / 2.0, 0.5)
    return 1.0 - tail if value > 0 else tail


def student_t_ppf(probability: float, degrees_of_freedom: float) -> float:
    if not 0 < probability < 1:
        raise ValueError("probability must be between zero and one")
    if probability == 0.5:
        return 0.0
    sign = 1.0 if probability > 0.5 else -1.0
    target = probability if sign > 0 else 1.0 - probability
    low, high = 0.0, max(1.0, NormalDist().inv_cdf(target) * 1.5)
    while student_t_cdf(high, degrees_of_freedom) < target:
        high *= 2.0
    for _ in range(90):
        mid = (low + high) / 2.0
        if student_t_cdf(mid, degrees_of_freedom) < target:
            low = mid
        else:
            high = mid
    return sign * (low + high) / 2.0


def normal_interval(effect: float, se: float, confidence: float) -> tuple[float, float]:
    critical = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    return effect - critical * se, effect + critical * se


__all__ = [
    "normal_interval",
    "regularized_beta",
    "student_t_cdf",
    "student_t_ppf",
]
