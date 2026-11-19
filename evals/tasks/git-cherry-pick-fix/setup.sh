#!/bin/sh
set -e
git init -q -b main
git add -A
git commit -q -m "Release 1.4"
git branch release/1.4
cat > src/main/java/com/example/GiftCard.java <<'EOF'
package com.example;

public record GiftCard(String code, long cents) {}
EOF
git add -A && git commit -q -m "Add gift cards"
cat > src/main/java/com/example/Cart.java <<'EOF'
package com.example;

import java.util.List;

public final class Cart {
    public long total(List<Long> items) {
        if (items == null) {
            return 0;
        }
        return items.stream().mapToLong(Long::longValue).sum();
    }
}
EOF
cat > src/test/java/com/example/CartTest.java <<'EOF'
package com.example;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class CartTest {
    @Test
    void addsItems() {
        assertEquals(30, new Cart().total(List.of(10L, 20L)));
    }

    @Test
    void emptyCartCostsNothing() {
        assertEquals(0, new Cart().total(null));
    }
}
EOF
git add -A && git commit -q -m "Fix the NPE on an empty cart"
cat > src/main/java/com/example/Loyalty.java <<'EOF'
package com.example;

public final class Loyalty {
    public long points(long cents) {
        return cents / 100;
    }
}
EOF
git add -A && git commit -q -m "Add loyalty points"
