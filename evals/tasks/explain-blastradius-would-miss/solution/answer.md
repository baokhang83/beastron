A would-miss is a test that caught a real regression or an injected mutant, but that test selection chose
    not to run: it would have been skipped. Would-misses are measured by replaying a project's commit history
    (historical replay of consecutive commit pairs) with bounded mutation validation, comparing what selection
    skipped against the ground truth.
