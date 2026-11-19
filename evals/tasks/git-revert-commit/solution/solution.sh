#!/bin/sh
set -e
git revert --no-edit "$(git log --format=%H --grep="Switch prices to USD")" >/dev/null
