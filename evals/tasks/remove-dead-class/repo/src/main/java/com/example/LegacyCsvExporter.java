package com.example;

/** The old CSV export, replaced by ReportService. */
public final class LegacyCsvExporter {
    public String export(String name, long cents) {
        return name + ";" + cents;
    }
}
