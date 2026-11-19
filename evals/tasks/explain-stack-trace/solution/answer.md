TAX_RATES in invoice.py has no entry for 'CH' (Switzerland), so looking up the order's country raises a KeyError. Fix it by adding a CH rate to TAX_RATES, or by handling countries that are missing.
