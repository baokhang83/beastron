import time


class PaymentHandler:
    def __init__(self, gateway):
        self.gateway = gateway

    def process(self, payment):
        for attempt in range(3):
            try:
                return self.gateway.charge(payment)
            except TimeoutError:
                if attempt < 2:
                    retry_delay = 2
                    time.sleep(retry_delay)
        raise RuntimeError("payment failed after 3 attempts")
