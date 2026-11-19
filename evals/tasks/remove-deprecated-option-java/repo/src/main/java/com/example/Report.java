package com.example;

public final class Report {
    private Report() {}

    public static String build(String month, boolean legacy) {
        if (legacy) {
            return "REPORT " + month.toUpperCase();
        }
        return "Report for " + month;
    }
}
