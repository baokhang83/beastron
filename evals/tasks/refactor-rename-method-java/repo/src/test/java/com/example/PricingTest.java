package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class PricingTest {
    @Test
    void totalsTheLines() {
        assertEquals(1000, Pricing.calcTot(List.of(new long[] {2, 300}, new long[] {1, 400})));
    }

    @Test
    void invoiceShowsTheTotal() {
        assertEquals("Total: 10.00", new Invoice().render(List.of(new long[] {2, 300}, new long[] {1, 400})));
    }
}
