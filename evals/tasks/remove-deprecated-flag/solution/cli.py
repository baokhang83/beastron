import argparse

from report import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="report")
    parser.add_argument("month")
    args = parser.parse_args(argv)
    print(build_report(args.month))


if __name__ == "__main__":
    main()
