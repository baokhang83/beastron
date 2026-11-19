package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class SlugTest {
    @Test
    void lowercasesAndJoinsWords() {
        assertEquals("hello-world", Slug.slugify("Hello World"));
    }

    @Test
    void dropsPunctuationAndOuterSeparators() {
        assertEquals("hello-world", Slug.slugify("  Hello, World!  "));
    }

    @Test
    void emptyInput() {
        assertEquals("", Slug.slugify(""));
        assertEquals("", Slug.slugify(null));
    }
}
