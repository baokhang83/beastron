package com.example;

import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

public final class Users {
    private static final Pattern EMAIL = Pattern.compile("[^@\\s]+@[^@\\s]+\\.[a-z]{2,}");

    public Map<String, String> register(String email) {
        String normalized = email.strip().toLowerCase(Locale.ROOT);
        if (!EMAIL.matcher(normalized).matches()) {
            throw new IllegalArgumentException("invalid email: " + normalized);
        }
        return Map.of("email", normalized);
    }

    public Map<String, String> updateEmail(Map<String, String> user, String email) {
        String normalized = email.strip().toLowerCase(Locale.ROOT);
        if (!EMAIL.matcher(normalized).matches()) {
            throw new IllegalArgumentException("invalid email: " + normalized);
        }
        return Map.of("name", user.getOrDefault("name", ""), "email", normalized);
    }
}
