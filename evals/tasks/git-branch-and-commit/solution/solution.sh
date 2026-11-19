#!/bin/sh
set -e
git checkout -q -b fix/rounding
git commit -q -am "Round monthly totals to the cent"
