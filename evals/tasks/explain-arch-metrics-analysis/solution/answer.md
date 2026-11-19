It reads the compiled bytecode: the .class files, parsed with the ASM library, so it sees the dependencies of
    the compiled result instead of guessing them from source text. The project must be compiled first (mvn compile),
    so that its classes are in target/classes, and the server is pointed at it with PROJECT_ROOT (otherwise it
    analyzes its working directory).
