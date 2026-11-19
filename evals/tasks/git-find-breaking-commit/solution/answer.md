Commit 656d88f ("Speed up total()") broke it: it rewrote total() in totals.py and dropped the discount, so the discount is no longer applied.
