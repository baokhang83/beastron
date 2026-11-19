Add io.github.baokhang83.blastradius:blastradius-maven-plugin to <build><plugins>, with the select goal in the
    process-test-classes phase and baseRef set to the base branch (e.g. main), then run mvn verify as usual (JDK 21+,
    in a Git repository). CI must keep the .blastradius/ folder: persist it after a successful base-branch run and
    restore it before a pull request build (or use the S3 index store), and the checkout needs the base reference and
    enough history for the merge base. Without a usable index it runs the full suite.
