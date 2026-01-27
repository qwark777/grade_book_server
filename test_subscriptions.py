#!/usr/bin/env python3
"""
Test script for subscription system
"""

import asyncio
import sys
import os

# Add the app directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))

from app.db.connection import get_db_connection
from app.core.entitlements import check_entitlement, get_school_id_for_user
from app.models.user import User


async def test_subscription_system():
    """Test the subscription system"""
    print("Testing subscription system...")
    
    # Test database connection
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Check if tables exist
            await cursor.execute("SHOW TABLES LIKE 'subscription_%'")
            tables = await cursor.fetchall()
            print(f"Found {len(tables)} subscription tables:")
            for table in tables:
                print(f"  - {list(table.values())[0]}")
            
            # Check plans
            await cursor.execute("SELECT COUNT(*) as count FROM subscription_plans")
            plans_count = await cursor.fetchone()
            print(f"Plans in database: {plans_count['count']}")
            
            # Check entitlements
            await cursor.execute("SELECT COUNT(*) as count FROM plan_entitlements")
            entitlements_count = await cursor.fetchone()
            print(f"Entitlements in database: {entitlements_count['count']}")
            
            # Check school subscriptions
            await cursor.execute("SELECT COUNT(*) as count FROM school_subscriptions")
            subscriptions_count = await cursor.fetchone()
            print(f"School subscriptions: {subscriptions_count['count']}")
            
            # List plans
            await cursor.execute("SELECT name, price_monthly, price_yearly FROM subscription_plans ORDER BY price_monthly")
            plans = await cursor.fetchall()
            print("\nAvailable plans:")
            for plan in plans:
                print(f"  - {plan['name']}: ${plan['price_monthly']}/month, ${plan['price_yearly']}/year")
            
            # List some entitlements
            await cursor.execute("""
                SELECT sp.name as plan_name, pe.entitlement_key, pe.entitlement_value
                FROM plan_entitlements pe
                JOIN subscription_plans sp ON pe.plan_id = sp.id
                WHERE pe.entitlement_key IN ('analytics.full', 'ai.quota', 'exports.csv')
                ORDER BY sp.name, pe.entitlement_key
            """)
            entitlements = await cursor.fetchall()
            print("\nSample entitlements:")
            for ent in entitlements:
                print(f"  - {ent['plan_name']}: {ent['entitlement_key']} = {ent['entitlement_value']}")
                
    finally:
        conn.close()
    
    print("\nSubscription system test completed!")


if __name__ == "__main__":
    asyncio.run(test_subscription_system())
