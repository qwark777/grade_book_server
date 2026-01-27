import asyncio
from datetime import datetime, timedelta
from app.db.connection import get_db_connection


async def seed_subscription_plans():
    """Seed subscription plans and their entitlements"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Clear existing data
            await cursor.execute("DELETE FROM plan_entitlements")
            await cursor.execute("DELETE FROM subscription_plans")
            
            # Insert subscription plans
            plans_data = [
                {
                    "name": "Free",
                    "description": "Базовый план для небольших школ",
                    "price_monthly": 0.0,
                    "price_yearly": 0.0,
                    "currency": "USD"
                },
                {
                    "name": "Pro",
                    "description": "Полнофункциональный план для школ",
                    "price_monthly": 50.0,
                    "price_yearly": 500.0,
                    "currency": "USD"
                },
                {
                    "name": "Enterprise",
                    "description": "План для сетей школ и владельцев",
                    "price_monthly": 200.0,
                    "price_yearly": 2000.0,
                    "currency": "USD"
                },
                {
                    "name": "Student Plus",
                    "description": "Дополнительные возможности для учеников",
                    "price_monthly": 5.0,
                    "price_yearly": 50.0,
                    "currency": "USD"
                },
                {
                    "name": "Teacher Pro",
                    "description": "Расширенные инструменты для учителей",
                    "price_monthly": 10.0,
                    "price_yearly": 100.0,
                    "currency": "USD"
                }
            ]
            
            plan_ids = {}
            for plan_data in plans_data:
                await cursor.execute("""
                    INSERT INTO subscription_plans (name, description, price_monthly, price_yearly, currency)
                    VALUES (%(name)s, %(description)s, %(price_monthly)s, %(price_yearly)s, %(currency)s)
                """, plan_data)
                plan_id = cursor.lastrowid
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
                
                # Student Plus entitlements
                ("Student Plus", "ai.personal", "true"),
                ("Student Plus", "goals.tracking", "true"),
                ("Student Plus", "parent.reports", "true"),
                ("Student Plus", "study.analytics", "true"),
                ("Student Plus", "achievements.extra", "true"),
                
                # Teacher Pro entitlements
                ("Teacher Pro", "reports.subject", "true"),
                ("Teacher Pro", "exports.advanced", "true"),
                ("Teacher Pro", "grading.templates", "true"),
                ("Teacher Pro", "analytics.subject", "true"),
                ("Teacher Pro", "bulk.operations", "true")
            ]
            
            for plan_name, entitlement_key, entitlement_value in entitlements_data:
                plan_id = plan_ids[plan_name]
                await cursor.execute("""
                    INSERT INTO plan_entitlements (plan_id, entitlement_key, entitlement_value)
                    VALUES (%s, %s, %s)
                """, (plan_id, entitlement_key, entitlement_value))
            
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


if __name__ == "__main__":
    asyncio.run(main())
