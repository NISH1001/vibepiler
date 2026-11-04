"""Leftovers a coding agent leaves inside function bodies: the liveness pass's fixture. Each function is one case."""


def never_read():  # dead-store: assigned and never read
    items = []
    return 0


def overwritten():  # dead-store: overwritten before it is read (ruff does not flag this one)
    data = []
    data = load()
    return data


def call_kept():  # dead-store, but the call may matter: simplify, frees no lines
    result = compute()
    return 0


def notify(message, channel):  # unused-param: the trailing `channel`
    return message


def callback(err, value):  # not reported: `err` is a placeholder before a read parameter
    return value


def unpack(pair):  # dead-store: a destructuring with no read sibling, reported once (ruff does not flag this one)
    first, second = pair
    return 0


def read_config(path):  # dead-store: the `with` target is never read
    with open(path) as fh:
        return 1


def safe():  # dead-store: the `except` target is never read
    try:
        risky()
    except ValueError as exc:
        return None
    return 1


def experiment():  # dead-branch: a branch on a constant
    if False:
        print("the old path")
    return 1


def early():  # unreachable-code: after the return
    return 1
    print("never")


class Service:  # unused-attribute: `cache` is written and never read
    def __init__(self):
        self.cache = {}
        self.name = "svc"

    def describe(self):
        return self.name
