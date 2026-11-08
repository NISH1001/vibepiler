from .compat import quote
from .compat import json as cj

try:
    from fastlib import speedy
except ImportError:
    def speedy(x):
        return x


def run(x):
    return quote(x) + cj.dumps(speedy(x))


if __name__ == "__main__":
    run("a")
