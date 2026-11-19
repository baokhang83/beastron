#!/bin/sh
set -e
git checkout -q release/1.4
git cherry-pick -x "$(git log main --format=%H --grep='Fix the NPE')" >/dev/null
git checkout -q main
