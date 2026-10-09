import asyncio
from datetime import datetime, timedelta
from app.db.connection import get_db_connection


async def seed_subscription_plans():
    """Seed subscription plans and their entitlements"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Insert subscription plans
            plans_data = [
                {
                    "name": "Free",
                    "description": "Базовый план для небольших школ",
                    "price_monthly": 0.0,
                    "price_yearly": 0.0,
                    "currency": "USD",
                    "is_active": True,
                },
                {
                    "name": "Pro",
                    "description": "Полнофункциональный план для школ",
                    "price_monthly": 50.0,
                    "price_yearly": 500.0,
                    "currency": "USD",
                    "is_active": True,
                },
                {
                    "name": "Enterprise",
                    "description": "План для сетей школ и владельцев",
                    "price_monthly": 200.0,
                    "price_yearly": 2000.0,
                    "currency": "USD",
                    "is_active": True,
                }
            ]
            
            plan_ids = {}
            for plan_data in plans_data:
                await cursor.execute("""
                    INSERT INTO subscription_plans
                    (name, description, price_monthly, price_yearly, currency, is_active)
                    VALUES
                    (%(name)s, %(description)s, %(price_monthly)s, %(price_yearly)s, %(currency)s, %(is_active)s)
                    ON DUPLICATE KEY UPDATE
                        description = %(description)s,
                        price_monthly = %(price_monthly)s,
                        price_yearly = %(price_yearly)s,
                        currency = %(currency)s,
                        is_active = %(is_active)s
                """, plan_data)
                await cursor.execute(
                    "SELECT id FROM subscription_plans WHERE name = %s",
                    (plan_data["name"],),
                )
                plan_row = await cursor.fetchone()
                plan_id = plan_row["id"]
                plan_ids[plan_data["name"]] = plan_id
            
            # Insert entitlements for each plan
            entitlements_data = [
                # Free plan entitlements
                ("Free", "classes.max", "10"),
                ("Free", "students.max", "100"),
                ("Free", "teachers.max", "5"),
                ("Free", "ai.quota", "200"),
                ("Free", "exports.csv", "2"),
                ("Free", "analytics.basic", "true"),
                ("Free", "analytics.full", "false"),
                ("Free", "roles.basic", "true"),
                ("Free", "roles.advanced", "false"),
                ("Free", "storage.mb", "1024"),
                ("Free", "messages.daily", "1000"),
                
                # Pro plan entitlements
                ("Pro", "classes.unlimited", "true"),
                ("Pro", "students.max", "500"),
                ("Pro", "teachers.max", "25"),
                ("Pro", "ai.quota", "10000"),
                ("Pro", "exports.csv", "unlimited"),
                ("Pro", "analytics.basic", "true"),
                ("Pro", "analytics.full", "true"),
                ("Pro", "roles.basic", "true"),
                ("Pro", "roles.advanced", "true"),
                ("Pro", "storage.mb", "102400"),
                ("Pro", "messages.daily", "10000"),
                ("Pro", "timetable.changes", "true"),
                ("Pro", "attendance.tracking", "true"),
                ("Pro", "reports.advanced", "true"),
                
                # Enterprise plan entitlements
                ("Enterprise", "classes.unlimited", "true"),
                ("Enterprise", "students.unlimited", "true"),
                ("Enterprise", "teachers.unlimited", "true"),
                ("Enterprise", "ai.quota", "unlimited"),
                ("Enterprise", "exports.csv", "unlimited"),
                ("Enterprise", "analytics.basic", "true"),
                ("Enterprise", "analytics.full", "true"),
                ("Enterprise", "roles.basic", "true"),
                ("Enterprise", "roles.advanced", "true"),
                ("Enterprise", "storage.unlimited", "true"),
                ("Enterprise", "messages.unlimited", "true"),
                ("Enterprise", "timetable.changes", "true"),
                ("Enterprise", "attendance.tracking", "true"),
                ("Enterprise", "reports.advanced", "true"),
                ("Enterprise", "sso.enabled", "true"),
                ("Enterprise", "scim.enabled", "true"),
                ("Enterprise", "audit.logs", "true"),
                ("Enterprise", "webhooks", "true"),
                ("Enterprise", "api.access", "true"),
                ("Enterprise", "multi.school", "true"),
                ("Enterprise", "owner.dashboard", "true"),
            ]
            
            for plan_name, entitlement_key, entitlement_value in entitlements_data:
                plan_id = plan_ids[plan_name]
                await cursor.execute("""
                    INSERT INTO plan_entitlements (plan_id, entitlement_key, entitlement_value)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        entitlement_value = %s
                """, (
                    plan_id,
                    entitlement_key,
                    entitlement_value,
                    entitlement_value,
                ))
            
            await conn.commit()
            print("Subscription plans and entitlements seeded successfully")
            
    finally:
        conn.close()


async def create_default_subscriptions():
    """Create default subscriptions for existing schools"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Get all schools without subscriptions
            await cursor.execute("""
                SELECT s.id FROM schools s
                LEFT JOIN school_subscriptions ss ON s.id = ss.school_id
                WHERE ss.id IS NULL
            """)
            schools = await cursor.fetchall()
            
            # Get Free plan ID
            await cursor.execute("SELECT id FROM subscription_plans WHERE name = 'Free'")
            free_plan = await cursor.fetchone()
            
            if not free_plan:
                print("Free plan not found, skipping default subscriptions")
                return
            
            free_plan_id = free_plan["id"]
            
            # Create trial subscriptions for all schools
            for school in schools:
                school_id = school["id"]
                period_start = datetime.now()
                period_end = period_start + timedelta(days=30)  # 30-day trial
                
                await cursor.execute("""
                    INSERT INTO school_subscriptions 
                    (school_id, plan_id, status, current_period_start, current_period_end)
                    VALUES (%s, %s, %s, %s, %s)
                """, (school_id, free_plan_id, "trial", period_start, period_end))
            
            await conn.commit()
            print(f"Created default subscriptions for {len(schools)} schools")
            
    finally:
        conn.close()


async def main():
    """Main function to seed all subscription data"""
    print("Seeding subscription data...")
    await seed_subscription_plans()
    await create_default_subscriptions()
    print("Subscription seeding completed!")


async def ensure_subscription_data():
    """Initialize the catalog once and give unsubscribed schools a trial."""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT COUNT(*) AS count FROM subscription_plans")
            row = await cursor.fetchone()
            count = row["count"] if isinstance(row, dict) else row[0]
    finally:
        conn.close()
    if int(count or 0) == 0:
        await seed_subscription_plans()
    await create_default_subscriptions()


if __name__ == "__main__":
    asyncio.run(main())
