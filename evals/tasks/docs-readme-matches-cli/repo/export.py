import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="export")
    parser.add_argument("statement", help="the bank statement CSV")
    parser.add_argument("--format", choices=["csv", "json"], default="csv", help="output format")
    parser.add_argument("--out", metavar="PATH", help="write here instead of standard output")
    return parser.parse_args(argv)
