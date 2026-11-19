A Boost is a named, bounded operation (an index rebuild, a batch, a migration) that needs extra memory now
    and gives it back when it's done. The operator declares the allowed Boosts in the policy, with a profile, a heap
    target and time limits. The application asks for one in Java through WardenBoostClient.acquireBoost(name,
    timeout), which returns a BoostLease once the request is granted; closing the lease requests completion.
