package com.example;

import static com.example.Pricing.calcTot;

import java.util.List;

public final class Invoice {
    public String render(List<long[]> lines) {
        long cents = calcTot(lines);
        return String.format("Total: %d.%02d", cents / 100, cents % 100);
    }
}
