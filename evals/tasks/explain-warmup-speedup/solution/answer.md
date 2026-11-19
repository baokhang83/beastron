It trains an AppCDS or Project Leyden AOT cache once, inside the cluster, and gives that cache to every
    replica of the unchanged JVM application, so pods skip much of the class loading and linking at start. You
    enable it with annotations on the Deployment's pod template (warmup.mnemo.io/enabled, store, exit-mode). A pod
    uses the cache only when it matches its launch exactly, and starts the ordinary way otherwise.
