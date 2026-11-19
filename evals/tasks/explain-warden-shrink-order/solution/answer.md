It shrinks the JVM first: it lowers SoftMaxHeapSize, forces a GC and uncommits memory, then verifies that the
    RSS really dropped below the target. Only then does it lower the pod's cgroup with an in-place resize of the
    request and limit. In the other order the cgroup limit would fall below what the JVM still uses, and the pod is
    OOMKilled. Growing goes the other way: resize the pod up first, then raise the JVM's soft max.
