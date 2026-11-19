# money-core

Money amounts without floating-point errors.

## Installation

Requires Java 17 or later.

```xml
<dependency>
  <groupId>io.example.money</groupId>
  <artifactId>money-core</artifactId>
  <version>2.3.1</version>
</dependency>
```

## Usage

```java
Money total = new Money(1999).plus(new Money(1));
```
