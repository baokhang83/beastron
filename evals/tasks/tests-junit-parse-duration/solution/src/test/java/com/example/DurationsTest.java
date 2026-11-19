package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import org.junit.jupiter.api.Test;

class DurationsTest {
    @Test
    void singleUnits() {
        assertEquals(7200, Durations.parse("2h"));
        assertEquals(900, Durations.parse("15m"));
        assertEquals(45, Durations.parse("45s"));
    }

    @Test
    void combinations() {
        assertEquals(5400, Durations.parse("1h30m"));
        assertEquals(3661, Durations.parse("1h1m1s"));
    }

    @Test
    void invalidInput() {
        for (String bad : new String[] {"", "1x", "h1", "1.5h", "ten minutes"}) {
            assertThrows(IllegalArgumentException.class, () -> Durations.parse(bad), bad);
        }
    }
}
