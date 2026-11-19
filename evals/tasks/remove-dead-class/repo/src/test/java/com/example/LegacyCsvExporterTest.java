package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class LegacyCsvExporterTest {
    @Test
    void exports() {
        assertEquals("rent;120000", new LegacyCsvExporter().export("rent", 120000));
    }
}
