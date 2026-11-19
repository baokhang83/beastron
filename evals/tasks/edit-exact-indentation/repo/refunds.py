import time


class RefundHandler:
    def __init__(self, gateway):
        self.gateway = gateway

    def process(self, refund):
        for attempt in range(3):
            try:
                return self.gateway.refund(refund)
            except TimeoutError:
                if attempt < 2:
                    retry_delay = 2
                    time.sleep(retry_delay)
        raise RuntimeError("refund failed after 3 attempts")
