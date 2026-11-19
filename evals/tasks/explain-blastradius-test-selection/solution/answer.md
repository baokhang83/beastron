A Java agent records the class dependencies that each test touches during a test run on the base branch.
    For a change branch, Blastradius compares the Git merge base with HEAD and selects the tests whose recorded
    dependencies include changed classes, plus new or modified tests, with some extra conservative rules.
    When the baseline data is missing or unusable it falls back to a full run of the suite.
