package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import java.util.Map;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;

class UsersTest {
    @Test
    void registerNormalizes() {
        assertEquals(Map.of("email", "ada@example.org"), new Users().register(" Ada@Example.org "));
    }

    @Test
    void updateKeepsTheName() {
        assertEquals(Map.of("name", "Ada", "email", "new@b.co"),
                new Users().updateEmail(Map.of("name", "Ada", "email", "a@b.co"), "new@b.co"));
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "no-at-sign", "a@b", "a b@c.de"})
    void invalidEmailsAreRejected(String bad) {
        assertThrows(IllegalArgumentException.class, () -> new Users().register(bad));
        assertThrows(IllegalArgumentException.class, () -> new Users().updateEmail(Map.of(), bad));
    }
}
