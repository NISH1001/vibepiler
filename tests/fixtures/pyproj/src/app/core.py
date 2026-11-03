from abc import ABC, abstractmethod
from app.utils import helper, _impl as impl_alias
from app import main as entry


class Base(ABC):
    @abstractmethod
    def run(self, data):
        ...


class Impl(Base):
    def run(self, data):
        return helper(data)

    def __repr__(self):
        return "Impl()"


def process(x):
    if x is None:
        return 0
    total = 0
    for i in range(x):
        if i % 2 == 0 and i % 3 == 0:
            total += i
        elif i % 5 == 0:
            total -= i
        else:
            try:
                total += impl_alias(i)
            except ValueError:
                total += 1
            except Exception as exc:
                raise
    while total > 100:
        total //= 2
    return total if total else -1


def uses_entry():
    return entry.main()
