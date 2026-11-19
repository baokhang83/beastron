package com.example;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class Durations {
    private static final Pattern WHOLE = Pattern.compile("(\\d+[hms])+");
    private static final Pattern PART = Pattern.compile("(\\d+)([hms])");

    private Durations() {}

    /** "1h30m" -> 5400 seconds. Units: h, m, s. */
    public static long parse(String text) {
        String t = text == null ? "" : text.strip();
        if (t.isEmpty() || !WHOLE.matcher(t).matches()) {
            throw new IllegalArgumentException("invalid duration: " + text);
        }
        long seconds = 0;
        Matcher m = PART.matcher(t);
        while (m.find()) {
            long n = Long.parseLong(m.group(1));
            switch (m.group(2)) {
                case "h" -> seconds += n * 3600;
                case "m" -> seconds += n * 60;
                default -> seconds += n;
            }
        }
        return seconds;
    }
}
