package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void addsItems() {
        assertEquals(30, new Cart().total(List.of(10L, 20L)));
    }
}
