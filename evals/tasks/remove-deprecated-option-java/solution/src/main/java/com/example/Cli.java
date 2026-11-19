package com.example;

public final class Cli {
    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("usage: report MONTH");
            System.exit(2);
        }
        System.out.println(Report.build(args[0]));
    }
}
