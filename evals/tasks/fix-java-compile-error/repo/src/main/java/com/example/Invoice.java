package com.example;

import java.util.List;

/** An invoice made of line amounts in cents. */
public class Invoice {
    private final List<Integer> amountsInCents;

    public Invoice(List<Integer> amountsInCents) {
        this.amountsInCents = List.copyOf(amountsInCents);
    }

    public int totalInCents() {
        int total = 0;
        for (int amount : amountsInCents) {
            total += amount;
        }
        return total;
    }

    /** The total as text with two decimals, e.g. "12.34". */
    public String formattedTotal() {
        return totalInCents() / 100.0;
    }
}
