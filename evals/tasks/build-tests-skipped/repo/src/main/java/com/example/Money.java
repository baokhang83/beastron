package com.example;

/** An amount in cents. */
public record Money(long cents) {
    public Money plus(Money other) {
        return new Money(cents + other.cents);
    }
}
