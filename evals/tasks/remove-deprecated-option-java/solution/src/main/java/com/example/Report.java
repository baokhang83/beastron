package com.example;

public final class Report {
    private Report() {}

    public static String build(String month) {
        return "Report for " + month;
    }
}
