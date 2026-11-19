package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class ReportTest {
    @Test
    void formatsTheReport() {
        assertEquals("Report for 2026-11", Report.build("2026-11", false));
    }

    @Test
    void legacyModeUsesTheOldLayout() {
        assertEquals("REPORT 2026-11", Report.build("2026-11", true));
    }
}
