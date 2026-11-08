from abc import ABC, abstractmethod


def helper(x):
    return x + 1


class Base(ABC):
    @abstractmethod
    def do_transform(self, x):
        """Override me."""

    def __call__(self, x):
        return self.do_transform(x)


class Mid(Base):
    pass


class Leaf(Mid):
    def do_transform(self, x):
        return helper(x)


class Direct(Base):
    def do_transform(self, x):
        return helper(x) * 2


if __name__ == "__main__":
    Leaf()(1)
    Direct()(1)
