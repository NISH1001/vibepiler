import os
import json
from typing import Any


def helper(x):
    return _impl(x)


def _impl(x):
    return json.dumps(x)


def unused_fn(a, b):
    result = []
    for item in a:
        if item in b:
            result.append(item)
        else:
            result.append(None)
    return result


def dup_a(items, threshold):
    out = []
    for item in items:
        if item > threshold:
            out.append(item * 2)
        else:
            out.append(item)
    return sorted(out)


def dup_b(values, limit):
    acc = []
    for value in values:
        if value > limit:
            acc.append(value * 2)
        else:
            acc.append(value)
    return sorted(acc)


def swallow(x):
    try:
        return int(x)
    except Exception:
        pass


def reraise(x):
    try:
        return int(x)
    except Exception as e:
        raise
