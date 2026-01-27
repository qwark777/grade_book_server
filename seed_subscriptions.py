#!/usr/bin/env python3
"""
Script to seed subscription data into the database
Run this after setting up the database schema
"""

import asyncio
import sys
import os

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.seed_subscriptions import main

if __name__ == "__main__":
    print("Seeding subscription data...")
    asyncio.run(main())
    print("Done!")
