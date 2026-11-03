from dataclasses import dataclass


@dataclass
class Config:
    value: int = 0

    @classmethod
    def load(cls, path):
        return cls(value=len(path))


class OnlyOneMethod:
    def __init__(self, x):
        self.x = x

    def compute(self):
        return self.x * 2
