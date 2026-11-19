package com.example.shop;

import java.io.IOException;
import java.io.InputStream;
import java.time.Duration;
import java.util.Properties;

/** Settings: application.properties, then application-<profile>.properties, then environment variables. */
public final class Config {
    private final Properties props = new Properties();

    public Config() {
        load("application.properties");
        String profile = System.getenv("APP_PROFILE");
        if (profile != null && !profile.isBlank()) {
            load("application-" + profile + ".properties");
        }
    }

    private void load(String name) {
        try (InputStream in = Config.class.getClassLoader().getResourceAsStream(name)) {
            if (in != null) {
                props.load(in);
            }
        } catch (IOException e) {
            throw new IllegalStateException("can't read " + name, e);
        }
    }

    public Duration paymentTimeout() {
        String env = System.getenv("PAYMENT_TIMEOUT_SECONDS");
        String value = env != null ? env : props.getProperty("payment.timeout.seconds", "5");
        return Duration.ofSeconds(Long.parseLong(value));
    }

    public Duration retryDelay() {
        return Duration.ofSeconds(Long.parseLong(props.getProperty("retry.delay.seconds", "30")));
    }
}
