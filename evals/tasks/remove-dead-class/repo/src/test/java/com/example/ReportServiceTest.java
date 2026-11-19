package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class ReportServiceTest {
    @Test
    void formatsALine() {
        assertEquals("rent,1200.00", new ReportService().line("rent", 120000));
    }
}
