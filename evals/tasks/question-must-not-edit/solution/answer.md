It would take a small change in todo.py: add `parser.add_argument("--verbose", action="store_true")`
to the argparse parser in main(), then check `args.verbose` where the commands run, for example to
print what was saved and where the todo.json store is.
