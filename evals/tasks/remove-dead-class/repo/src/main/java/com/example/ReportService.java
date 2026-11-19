package com.example;

public final class ReportService {
    public String line(String name, long cents) {
        return String.format("%s,%d.%02d", name, cents / 100, cents % 100);
    }
}
