"""Entry point."""
import sys
import click
import requests
from app.core import process, Impl
from app import utils
from .models import Config


@click.command()
def cli():
    cfg = Config.load("x")
    print(process(cfg.value))


def main():
    data = requests.get("http://x").json()
    impl = Impl()
    impl.run(data)
    utils.helper(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
