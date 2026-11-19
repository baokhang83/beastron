package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class DiscountCalculatorTest {
    @Test
    void tenPercentOff() {
        assertEquals(1800, DiscountCalculator.apply(2000, 10));
    }

    @Test
    void noDiscount() {
        assertEquals(999, DiscountCalculator.apply(999, 0));
    }

    @Test
    void everythingOff() {
        assertEquals(0, DiscountCalculator.apply(999, 100));
    }

    @Test
    void roundsDownToTheCent() {
        assertEquals(669, DiscountCalculator.apply(999, 33)); // 669.33 rounded down
    }

    @Test
    void rejectsInvalidPercentages() {
        assertThrows(IllegalArgumentException.class, () -> DiscountCalculator.apply(100, 101));
    }
}
