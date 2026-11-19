package com.example;

import java.util.ArrayList;
import java.util.List;

public final class Cart {
    private final List<long[]> lines = new ArrayList<>();

    public void add(long quantity, long cents) {
        lines.add(new long[] {quantity, cents});
    }

    public long total() {
        return Pricing.calculateTotal(lines);
    }
}
