"""A tiny to-do list on the command line."""
import argparse
import json
from pathlib import Path

STORE = Path("todo.json")


def load():
    return json.loads(STORE.read_text()) if STORE.exists() else []


def save(items):
    STORE.write_text(json.dumps(items, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(prog="todo")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="add an item")
    add.add_argument("text")
    commands.add_parser("list", help="show all items")
    args = parser.parse_args(argv)

    items = load()
    if args.command == "add":
        items.append(args.text)
        save(items)
    else:
        for number, item in enumerate(items, 1):
            print(f"{number}. {item}")


if __name__ == "__main__":
    main()
