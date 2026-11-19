package com.example;

public final class Cli {
    public static void main(String[] args) {
        boolean legacy = false;
        String month = null;
        for (String arg : args) {
            if (arg.equals("--legacy-mode")) {
                legacy = true; // deprecated: the old column layout
            } else {
                month = arg;
            }
        }
        if (month == null) {
            System.err.println("usage: report MONTH [--legacy-mode]");
            System.exit(2);
        }
        System.out.println(Report.build(month, legacy));
    }
}
