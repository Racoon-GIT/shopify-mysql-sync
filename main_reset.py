#!/usr/bin/env python3
# main_reset.py
"""
Entry point per reset varianti Shopify.
Wrapper per reset_variants.py con supporto per esecuzione locale.

Uso:
    # Con variabile d'ambiente
    PRODUCT_IDS="123456,789012" python main_reset.py

    # Oppure con argomento da linea di comando
    python main_reset.py 123456,789012
"""

import sys
import os

# Aggiungi directory corrente al path per import moduli src
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Supporto per argomenti da linea di comando
if len(sys.argv) > 1 and not os.getenv("PRODUCT_IDS"):
    os.environ["PRODUCT_IDS"] = sys.argv[1]

from reset_variants import main

if __name__ == "__main__":
    main()
