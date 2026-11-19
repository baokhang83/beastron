package com.example;

import java.util.List;

public final class Cart {
    public long total(List<Long> items) {
        return items.stream().mapToLong(Long::longValue).sum();
    }
}
