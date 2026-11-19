import argparse

from report import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="report")
    parser.add_argument("month")
    parser.add_argument("--legacy-mode", action="store_true", help="deprecated: the old column layout")
    args = parser.parse_args(argv)
    print(build_report(args.month, legacy=args.legacy_mode))


if __name__ == "__main__":
    main()
