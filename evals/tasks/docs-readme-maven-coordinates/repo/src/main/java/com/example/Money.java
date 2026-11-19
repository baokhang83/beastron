package com.example;

/** An amount in cents, so that sums don't drift like floating point. */
public record Money(long cents) {
    public Money plus(Money other) {
        return new Money(cents + other.cents);
    }
}
