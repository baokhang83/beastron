#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Release 1.0.0"
git tag v1.0.0
printf '\n\ndef export_json(rows):\n    return list(rows)\n' >> export.py && git commit -q -am "Add JSON export"
printf '\n\ndef safe_export(rows):\n    return export(rows or [])\n' >> export.py && git commit -q -am "Fix the crash on empty statements"
printf '\n# categories are matched with a precompiled pattern\n' >> export.py && git commit -q -am "Speed up category matching"
