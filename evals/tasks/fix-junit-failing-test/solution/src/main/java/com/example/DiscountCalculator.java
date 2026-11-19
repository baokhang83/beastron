package com.example;

/** Applies percentage discounts to prices in cents. */
public final class DiscountCalculator {
    private DiscountCalculator() {}

    /** The price after taking {@code percent} percent off, rounded down to the cent. */
    public static int apply(int priceInCents, int percent) {
        if (percent < 0 || percent > 100) {
            throw new IllegalArgumentException("percent must be between 0 and 100: " + percent);
        }
        return priceInCents * (100 - percent) / 100;
    }
}
