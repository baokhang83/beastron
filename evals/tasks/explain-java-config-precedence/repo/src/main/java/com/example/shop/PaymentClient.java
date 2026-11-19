package com.example.shop;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;

public final class PaymentClient {
    private final Config config = new Config();
    private final HttpClient http = HttpClient.newHttpClient();

    HttpRequest charge(URI provider) {
        return HttpRequest.newBuilder(provider).timeout(config.paymentTimeout()).build();
    }
}
