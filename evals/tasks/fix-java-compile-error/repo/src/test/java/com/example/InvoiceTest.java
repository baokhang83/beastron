package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class InvoiceTest {
    @Test
    void totalAddsAllLines() {
        assertEquals(1234, new Invoice(List.of(1000, 200, 34)).totalInCents());
    }

    @Test
    void formattedTotalHasTwoDecimals() {
        assertEquals("12.34", new Invoice(List.of(1000, 200, 34)).formattedTotal());
        assertEquals("5.00", new Invoice(List.of(500)).formattedTotal());
    }
}
