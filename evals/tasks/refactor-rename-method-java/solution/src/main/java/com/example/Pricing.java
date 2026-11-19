package com.example;

import java.util.List;

public final class Pricing {
    private Pricing() {}

    /** Each line is {quantity, unit price in cents}. */
    public static long calculateTotal(List<long[]> lines) {
        return lines.stream().mapToLong(l -> l[0] * l[1]).sum();
    }
}
