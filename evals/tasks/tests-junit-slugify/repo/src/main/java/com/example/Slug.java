package com.example;

import java.util.Locale;

public final class Slug {
    private Slug() {}

    /** "Hello, World!" -> "hello-world". */
    public static String slugify(String text) {
        if (text == null || text.isBlank()) {
            return "";
        }
        String lower = text.toLowerCase(Locale.ROOT);
        String dashed = lower.replaceAll("[^a-z0-9]+", "-");
        return dashed.replaceAll("(^-+|-+$)", "");
    }
}
