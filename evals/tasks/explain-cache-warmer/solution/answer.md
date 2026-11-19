It pre-warms a CI build from remote cache storage (GitHub Actions cache or S3): the ~/.m2 local repository,
    sibling target/classes bytecode and target/maven-status compiler state, restored only for modules that a
    git-diff blast-radius calculation shows are unaffected. It fails open: anything it can't warm confidently
    simply builds cold, as if the extension weren't installed.
