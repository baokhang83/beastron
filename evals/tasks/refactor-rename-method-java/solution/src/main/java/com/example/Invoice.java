package com.example;

import static com.example.Pricing.calculateTotal;

import java.util.List;

public final class Invoice {
    public String render(List<long[]> lines) {
        long cents = calculateTotal(lines);
        return String.format("Total: %d.%02d", cents / 100, cents % 100);
    }
}
