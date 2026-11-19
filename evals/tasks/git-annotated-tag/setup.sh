#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Release 1.2.0"
