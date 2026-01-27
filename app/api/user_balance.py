"""
API endpoints for user balance and purchased lessons
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel
from datetime import datetime
from decimal import Decimal
from app.db.connection import get_db_connection
from app.core.security import get_current_user
from app.models.user import UserInDB

router = APIRouter(prefix="/balance")


class BalanceResponse(BaseModel):
    user_id: int
    balance: float
    currency: str
    updated_at: datetime


class TransactionResponse(BaseModel):
    id: int
    user_id: int
    transaction_type: str
    amount: float
    balance_before: float
    balance_after: float
    currency: str
    description: Optional[str] = None
    reference_type: Optional[str] = None
    reference_id: Optional[int] = None
    created_at: datetime


class PurchasedLessonResponse(BaseModel):
    enrollment_id: int
    lesson_id: int
    lesson_title: str
    lesson_subject: str
    tutor_name: str
    purchase_price: Optional[float] = None
    purchase_date: Optional[datetime] = None
    status: str
    payment_status: str
    payment_method: Optional[str] = None


class BalanceDepositRequest(BaseModel):
    amount: float
    description: Optional[str] = None


@router.get("/me", response_model=BalanceResponse)
async def get_my_balance(current_user: UserInDB = Depends(get_current_user)):
    """Получить баланс текущего пользователя"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем баланс пользователя
            await cursor.execute("""
                SELECT user_id, balance, currency, updated_at
                FROM user_balance
                WHERE user_id = %s
            """, (current_user.id,))
            
            row = await cursor.fetchone()
            
            if row:
                return {
                    "user_id": row["user_id"],
                    "balance": float(row["balance"]),
                    "currency": row["currency"],
                    "updated_at": row["updated_at"],
                }
            else:
                # Создаем запись с балансом 0, если её нет
                await cursor.execute("""
                    INSERT INTO user_balance (user_id, balance, currency)
                    VALUES (%s, 0.00, 'RUB')
                """, (current_user.id,))
                await conn.commit()
                
                return {
                    "user_id": current_user.id,
                    "balance": 0.0,
                    "currency": "RUB",
                    "updated_at": datetime.now(),
                }
    finally:
        conn.close()


@router.get("/me/transactions", response_model=List[TransactionResponse])
async def get_my_transactions(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    transaction_type: Optional[str] = Query(None),
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить историю транзакций текущего пользователя"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            query = """
                SELECT 
                    id, user_id, transaction_type, amount,
                    balance_before, balance_after, currency,
                    description, reference_type, reference_id,
                    created_at
                FROM balance_transactions
                WHERE user_id = %s
            """
            params = [current_user.id]
            
            if transaction_type:
                query += " AND transaction_type = %s"
                params.append(transaction_type)
            
            query += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
            params.extend([limit, offset])
            
            await cursor.execute(query, params)
            rows = await cursor.fetchall()
            
            return [
                {
                    "id": row["id"],
                    "user_id": row["user_id"],
                    "transaction_type": row["transaction_type"],
                    "amount": float(row["amount"]),
                    "balance_before": float(row["balance_before"]),
                    "balance_after": float(row["balance_after"]),
                    "currency": row["currency"],
                    "description": row.get("description"),
                    "reference_type": row.get("reference_type"),
                    "reference_id": row.get("reference_id"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]
    finally:
        conn.close()


@router.get("/me/purchased-lessons", response_model=List[PurchasedLessonResponse])
async def get_my_purchased_lessons(
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список купленных занятий текущего пользователя"""
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем наличие колонок purchase_price, purchase_date, payment_method
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'lesson_enrollments' 
                AND COLUMN_NAME IN ('purchase_price', 'purchase_date', 'payment_method')
            """)
            available_columns = {row["COLUMN_NAME"] if isinstance(row, dict) else row[0] for row in await cursor.fetchall()}
            
            # Формируем SELECT с учетом доступных колонок
            purchase_price_col = "e.purchase_price" if "purchase_price" in available_columns else "NULL as purchase_price"
            purchase_date_col = "e.purchase_date" if "purchase_date" in available_columns else "NULL as purchase_date"
            payment_method_col = "e.payment_method" if "payment_method" in available_columns else "NULL as payment_method"
            order_by = "e.purchase_date DESC, e.enrolled_at DESC" if "purchase_date" in available_columns else "e.enrolled_at DESC"
            
            await cursor.execute(f"""
                SELECT 
                    e.id as enrollment_id,
                    e.lesson_id,
                    l.title as lesson_title,
                    l.subject as lesson_subject,
                    p.full_name as tutor_name,
                    {purchase_price_col},
                    {purchase_date_col},
                    e.status,
                    e.payment_status,
                    {payment_method_col}
                FROM lesson_enrollments e
                JOIN additional_lessons l ON e.lesson_id = l.id
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                WHERE e.student_id = %s
                ORDER BY {order_by}
            """, (current_user.id,))
            
            rows = await cursor.fetchall()
            
            return [
                {
                    "enrollment_id": row["enrollment_id"],
                    "lesson_id": row["lesson_id"],
                    "lesson_title": row["lesson_title"],
                    "lesson_subject": row["lesson_subject"],
                    "tutor_name": row.get("tutor_name", "Неизвестно"),
                    "purchase_price": float(row["purchase_price"]) if row.get("purchase_price") else None,
                    "purchase_date": row.get("purchase_date"),
                    "status": row["status"],
                    "payment_status": row["payment_status"],
                    "payment_method": row.get("payment_method"),
                }
                for row in rows
            ]
    finally:
        conn.close()


@router.post("/me/deposit")
async def deposit_balance(
    deposit: BalanceDepositRequest,
    current_user: UserInDB = Depends(get_current_user)
):
    """Пополнить баланс пользователя (для тестирования или админа)"""
    if deposit.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Получаем текущий баланс
            await cursor.execute("""
                SELECT balance FROM user_balance WHERE user_id = %s
            """, (current_user.id,))
            
            row = await cursor.fetchone()
            balance_before = float(row["balance"]) if row else 0.0
            
            # Вычисляем новый баланс
            balance_after = balance_before + deposit.amount
            
            # Создаем или обновляем баланс
            await cursor.execute("""
                INSERT INTO user_balance (user_id, balance, currency)
                VALUES (%s, %s, 'RUB')
                ON DUPLICATE KEY UPDATE balance = balance + %s
            """, (current_user.id, deposit.amount, deposit.amount))
            
            # Создаем транзакцию
            await cursor.execute("""
                INSERT INTO balance_transactions 
                (user_id, transaction_type, amount, balance_before, balance_after, currency, description, created_by)
                VALUES (%s, 'deposit', %s, %s, %s, 'RUB', %s, %s)
            """, (
                current_user.id,
                deposit.amount,
                balance_before,
                balance_after,
                deposit.description or "Пополнение баланса",
                current_user.id
            ))
            
            await conn.commit()
            
            return {
                "status": "success",
                "balance_before": balance_before,
                "balance_after": balance_after,
                "amount": deposit.amount
            }
    finally:
        conn.close()


@router.get("/users/{user_id}", response_model=BalanceResponse)
async def get_user_balance(
    user_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить баланс пользователя (только для админов или самого пользователя)"""
    # Проверка доступа - только свой баланс или админ
    if current_user.id != user_id:
        role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
        if role not in ("admin", "owner", "superadmin"):
            raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("""
                SELECT user_id, balance, currency, updated_at
                FROM user_balance
                WHERE user_id = %s
            """, (user_id,))
            
            row = await cursor.fetchone()
            
            if row:
                return {
                    "user_id": row["user_id"],
                    "balance": float(row["balance"]),
                    "currency": row["currency"],
                    "updated_at": row["updated_at"],
                }
            else:
                # Возвращаем баланс 0, если записи нет
                return {
                    "user_id": user_id,
                    "balance": 0.0,
                    "currency": "RUB",
                    "updated_at": datetime.now(),
                }
    finally:
        conn.close()


@router.get("/users/{user_id}/purchased-lessons", response_model=List[PurchasedLessonResponse])
async def get_user_purchased_lessons(
    user_id: int,
    current_user: UserInDB = Depends(get_current_user)
):
    """Получить список купленных занятий пользователя (только для админов или самого пользователя)"""
    # Проверка доступа
    if current_user.id != user_id:
        role = current_user["role"] if isinstance(current_user, dict) else getattr(current_user, "role", None)
        if role not in ("admin", "owner", "superadmin"):
            raise HTTPException(status_code=403, detail="Access denied")
    
    conn = await get_db_connection()
    try:
        async with conn.cursor() as cursor:
            # Проверяем наличие колонок purchase_price, purchase_date, payment_method
            await cursor.execute("""
                SELECT COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = DATABASE() 
                AND TABLE_NAME = 'lesson_enrollments' 
                AND COLUMN_NAME IN ('purchase_price', 'purchase_date', 'payment_method')
            """)
            available_columns = {row["COLUMN_NAME"] if isinstance(row, dict) else row[0] for row in await cursor.fetchall()}
            
            # Формируем SELECT с учетом доступных колонок
            purchase_price_col = "e.purchase_price" if "purchase_price" in available_columns else "NULL as purchase_price"
            purchase_date_col = "e.purchase_date" if "purchase_date" in available_columns else "NULL as purchase_date"
            payment_method_col = "e.payment_method" if "payment_method" in available_columns else "NULL as payment_method"
            order_by = "e.purchase_date DESC, e.enrolled_at DESC" if "purchase_date" in available_columns else "e.enrolled_at DESC"
            
            await cursor.execute(f"""
                SELECT 
                    e.id as enrollment_id,
                    e.lesson_id,
                    l.title as lesson_title,
                    l.subject as lesson_subject,
                    p.full_name as tutor_name,
                    {purchase_price_col},
                    {purchase_date_col},
                    e.status,
                    e.payment_status,
                    {payment_method_col}
                FROM lesson_enrollments e
                JOIN additional_lessons l ON e.lesson_id = l.id
                LEFT JOIN profiles p ON l.tutor_id = p.user_id
                WHERE e.student_id = %s
                ORDER BY {order_by}
            """, (user_id,))
            
            rows = await cursor.fetchall()
            
            return [
                {
                    "enrollment_id": row["enrollment_id"],
                    "lesson_id": row["lesson_id"],
                    "lesson_title": row["lesson_title"],
                    "lesson_subject": row["lesson_subject"],
                    "tutor_name": row.get("tutor_name", "Неизвестно"),
                    "purchase_price": float(row["purchase_price"]) if row.get("purchase_price") else None,
                    "purchase_date": row.get("purchase_date"),
                    "status": row["status"],
                    "payment_status": row["payment_status"],
                    "payment_method": row.get("payment_method"),
                }
                for row in rows
            ]
    finally:
        conn.close()
