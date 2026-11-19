package com.example;

import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

class ParityTest {
    @ParameterizedTest
    @ValueSource(ints = {0, 2, 4, -6})
    void evenNumbers(int n) {
        assertTrue(Parity.isEven(n));
    }
}
