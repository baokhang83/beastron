package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class MoneyTest {
    @Test
    void addsCents() {
        assertEquals(new Money(350), new Money(100).plus(new Money(250)));
    }

    @Test
    void zeroIsNeutral() {
        assertEquals(new Money(7), new Money(7).plus(new Money(0)));
    }
}
