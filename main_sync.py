#!/usr/bin/env python3
# main_sync.py
"""
Entry point per sincronizzazione Shopify -> MySQL.
Wrapper per shopify_to_mysql.py con supporto per esecuzione locale.
"""

import sys
import os

# Aggiungi directory corrente al path per import moduli src
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shopify_to_mysql import main

if __name__ == "__main__":
    main()
