from fastapi import FastAPI, HTTPException, Depends, status, Cookie, Cookie, Header
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
import os

app = FastAPI(title="Barrier Live Parking API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Подключаем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== Конфигурация ====================
DB_PATH = "parking.db"
SESSION_EXPIRE_MINUTES = 60

# Хранилище сессий
sessions: Dict[str, dict] = {}

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==================== Модели ====================
class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TicketRequest(BaseModel):
    email: EmailStr
    car_number: str = Field(..., alias="Номер автономии")
    passport_number: str = Field(..., alias="Номер паспорта")
    password: str = Field(..., min_length=6)
    
    @validator('car_number')
    def validate_car_number(cls, v):
        import re
        if not re.match(r'^\d{4} [A-Z]{2}-\d$', v):
            raise ValueError('Неверный формат номера. Пример: 1111 AA-1')
        return v

# ==================== Хелперы ====================
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed

def create_session(user_email: str, user_role: str = "user") -> str:
    token = secrets.token_urlsafe(32)
    sessions[token] = {
        "email": user_email,
        "role": user_role,
        "created_at": datetime.now(),
        "expires_at": datetime.now() + timedelta(minutes=SESSION_EXPIRE_MINUTES)
    }
    return token

# ЗАМЕНИТЕ функцию get_current_user на эту:
def get_current_user(authorization: Optional[str] = Header(None)):
    """Получение пользователя из заголовка Authorization: Bearer <token>"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Не авторизован - отсутствует заголовок Authorization")
    
    # Проверяем формат "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Неверный формат авторизации. Используйте: Bearer <token>")
    
    token = parts[1]
    
    if token not in sessions:
        raise HTTPException(status_code=401, detail="Не авторизован - неверный токен")
    
    session = sessions[token]
    if datetime.now() > session["expires_at"]:
        del sessions[token]
        raise HTTPException(status_code=401, detail="Сессия истекла")
    
    return session

# Также добавьте альтернативную функцию для получения пользователя из cookie (для обратной совместимости)
def get_current_user_from_cookie(token: Optional[str] = Cookie(None)):
    """Получение пользователя из Cookie (альтернативный метод)"""
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    if token not in sessions:
        raise HTTPException(status_code=401, detail="Не авторизован")
    
    session = sessions[token]
    if datetime.now() > session["expires_at"]:
        del sessions[token]
        raise HTTPException(status_code=401, detail="Сессия истекла")
    
    return session

def check_user_blocked(email: str):
    """Проверяет, не заблокирован ли пользователь"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT is_blocked, blocked_until FROM users WHERE email = ?", (email,))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if user and user["is_blocked"] == 1:
        if user["blocked_until"]:
            blocked_until = datetime.fromisoformat(user["blocked_until"])
            if datetime.now() > blocked_until:
                # Снимаем блокировку если время истекло
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET is_blocked = 0, blocked_until = NULL WHERE email = ?", (email,))
                conn.commit()
                cursor.close()
                conn.close()
                return False
        raise HTTPException(status_code=403, detail="Ваш аккаунт заблокирован")
    return False

def create_tables():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            car_number TEXT NOT NULL,
            passport_number TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            is_inside INTEGER DEFAULT 0,
            is_blocked INTEGER DEFAULT 0,
            blocked_until TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Таблица заявок
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            car_number TEXT NOT NULL,
            passport_number TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CHECK (status IN ('pending', 'approved', 'rejected'))
        )
    """)
    
    # Таблица свободных мест
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS parking_spots (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            free_count INTEGER NOT NULL DEFAULT 12,
            occupied_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO parking_spots (id, free_count, occupied_count) VALUES (1, 12, 0)")
    
    # Таблица админов
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            login TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO admins (id, login, password_hash) VALUES (1, 'admin', ?)", 
                   (hash_password("admin123"),))
    
    conn.commit()
    cursor.close()
    conn.close()

create_tables()

# ==================== Эндпоинты ====================
@app.get("/api/free-spots")
def get_free_spots():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT free_count, occupied_count FROM parking_spots WHERE id = 1")
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return {
        "free_spots": result["free_count"] if result else 12,
        "occupied_spots": result["occupied_count"] if result else 0
    }

@app.post("/api/login")
def login(user: UserLogin):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email, password_hash, is_blocked, blocked_until FROM users WHERE email = ?", (user.email,))
    db_user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not db_user or not verify_password(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    
    # Проверка блокировки
    if db_user["is_blocked"] == 1:
        if db_user["blocked_until"]:
            blocked_until = datetime.fromisoformat(db_user["blocked_until"])
            if datetime.now() > blocked_until:
                # Автоматическая разблокировка
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET is_blocked = 0, blocked_until = NULL WHERE email = ?", (user.email,))
                conn.commit()
                cursor.close()
                conn.close()
            else:
                raise HTTPException(status_code=403, detail=f"Аккаунт заблокирован до {blocked_until.strftime('%d.%m.%Y %H:%M')}")
        else:
            raise HTTPException(status_code=403, detail="Аккаунт заблокирован")
    
    token = create_session(user.email, "user")
    return {"message": "Успешный вход", "token": token}

@app.post("/api/logout")
def logout(token: Optional[str] = Cookie(None)):
    if token and token in sessions:
        del sessions[token]
    return {"message": "Выход выполнен"}

@app.get("/api/me")
def get_me(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT email, car_number, is_inside, is_blocked, blocked_until FROM users WHERE email = ?", (current_user["email"],))
    user = cursor.fetchone()
    cursor.close()
    conn.close()
    
    return {
        "email": user["email"],
        "car_number": user["car_number"],
        "is_inside": bool(user["is_inside"]),
        "is_blocked": bool(user["is_blocked"]),
        "blocked_until": user["blocked_until"]
    }

@app.post("/api/gate/enter")
def gate_enter(current_user: dict = Depends(get_current_user)):
    check_user_blocked(current_user["email"])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_inside FROM users WHERE email = ?", (current_user["email"],))
    user = cursor.fetchone()
    
    if user["is_inside"] == 1:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Машина уже на парковке")
    
    cursor.execute("SELECT free_count FROM parking_spots WHERE id = 1")
    spots = cursor.fetchone()
    
    if spots["free_count"] <= 0:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Нет свободных мест")
    
    cursor.execute("UPDATE users SET is_inside = 1 WHERE email = ?", (current_user["email"],))
    cursor.execute("UPDATE parking_spots SET free_count = free_count - 1, occupied_count = occupied_count + 1 WHERE id = 1")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": "Шлагбаум открыт. Добро пожаловать на парковку!"}

@app.post("/api/gate/exit")
def gate_exit(current_user: dict = Depends(get_current_user)):
    check_user_blocked(current_user["email"])
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_inside FROM users WHERE email = ?", (current_user["email"],))
    user = cursor.fetchone()
    
    if user["is_inside"] == 0:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Машина не на парковке")
    
    cursor.execute("UPDATE users SET is_inside = 0 WHERE email = ?", (current_user["email"],))
    cursor.execute("UPDATE parking_spots SET free_count = free_count + 1, occupied_count = occupied_count - 1 WHERE id = 1")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": "Шлагбаум открыт. Счастливого пути!"}

@app.post("/api/request-ticket", status_code=201)
def create_ticket(request: TicketRequest):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT email FROM users WHERE email = ?", (request.email,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь уже зарегистрирован")
    
    cursor.execute("SELECT id FROM tickets WHERE email = ? AND status = 'pending'", (request.email,))
    if cursor.fetchone():
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="У вас уже есть активная заявка")
    
    password_hash = hash_password(request.password)
    cursor.execute("""
        INSERT INTO tickets (email, car_number, passport_number, password_hash)
        VALUES (?, ?, ?, ?)
    """, (request.email, request.car_number, request.passport_number, password_hash))
    
    ticket_id = cursor.lastrowid
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": "Заявка отправлена на подтверждение", "ticket_id": ticket_id}

# ==================== АДМИН ЭНДПОИНТЫ ====================
def verify_admin(credentials: HTTPBasicCredentials = Depends(HTTPBasic())):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM admins WHERE login = ?", (credentials.username,))
    admin = cursor.fetchone()
    cursor.close()
    conn.close()
    
    if not admin or not verify_password(credentials.password, admin["password_hash"]):
        raise HTTPException(status_code=401, detail="Неверные данные администратора")
    return True

@app.get("/api/admin/pending-tickets")
def admin_get_pending_tickets(_: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, email, car_number, passport_number, status, 
               datetime(created_at, 'localtime') as created_at 
        FROM tickets WHERE status = 'pending'
    """)
    tickets = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return [dict(ticket) for ticket in tickets]

@app.get("/api/admin/users")
def admin_get_all_users(_: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, email, car_number, passport_number, is_inside, is_blocked, 
               blocked_until, created_at,
               datetime(created_at, 'localtime') as created_at_local
        FROM users 
        ORDER BY created_at DESC
    """)
    users = cursor.fetchall()
    cursor.close()
    conn.close()
    
    return [dict(user) for user in users]

@app.post("/api/admin/approve-ticket/{ticket_id}")
def admin_approve_ticket(ticket_id: int, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM tickets WHERE id = ? AND status = 'pending'", (ticket_id,))
    ticket = cursor.fetchone()
    if not ticket:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    try:
        cursor.execute("""
            INSERT INTO users (email, car_number, passport_number, password_hash, is_inside, is_blocked)
            VALUES (?, ?, ?, ?, 0, 0)
        """, (ticket["email"], ticket["car_number"], ticket["passport_number"], ticket["password_hash"]))
        
        cursor.execute("UPDATE tickets SET status = 'approved' WHERE id = ?", (ticket_id,))
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    
    cursor.close()
    conn.close()
    
    return {"message": f"Заявка {ticket_id} одобрена"}

@app.post("/api/admin/reject-ticket/{ticket_id}")
def admin_reject_ticket(ticket_id: int, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("UPDATE tickets SET status = 'rejected' WHERE id = ? AND status = 'pending'", (ticket_id,))
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": f"Заявка {ticket_id} отклонена"}

@app.post("/api/admin/block-user/{user_id}")
def admin_block_user(user_id: int, block_days: int = 7, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, email, is_inside FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user["is_inside"] == 1:
        cursor.execute("UPDATE users SET is_inside = 0 WHERE id = ?", (user_id,))
        cursor.execute("UPDATE parking_spots SET free_count = free_count + 1, occupied_count = occupied_count - 1 WHERE id = 1")
    
    blocked_until = (datetime.now() + timedelta(days=block_days)).isoformat()
    
    cursor.execute("""
        UPDATE users 
        SET is_blocked = 1, blocked_until = ? 
        WHERE id = ?
    """, (blocked_until, user_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": f"Пользователь {user['email']} заблокирован на {block_days} дней"}

@app.post("/api/admin/unblock-user/{user_id}")
def admin_unblock_user(user_id: int, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    cursor.execute("""
        UPDATE users 
        SET is_blocked = 0, blocked_until = NULL 
        WHERE id = ?
    """, (user_id,))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": f"Пользователь {user['email']} разблокирован"}

@app.delete("/api/admin/delete-user/{user_id}")
def admin_delete_user(user_id: int, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, email, is_inside FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user["is_inside"] == 1:
        raise HTTPException(status_code=400, detail="Нельзя удалить пользователя, машина которого на парковке")
    
    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": f"Пользователь {user['email']} удалён"}

@app.post("/api/admin/reset-password/{user_id}")
def admin_reset_password(user_id: int, new_password: str, _: bool = Depends(verify_admin)):
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Пароль должен быть не менее 6 символов")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, email FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    password_hash = hash_password(new_password)
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    
    conn.commit()
    cursor.close()
    conn.close()
    
    return {"message": f"Пароль для {user['email']} изменён на: {new_password}"}


# ==================== Отдача HTML ====================
@app.get("/", response_class=HTMLResponse)
async def root():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()

# ==================== Запуск ====================
if __name__ == "__main__":
    import uvicorn
    print("=" * 50)
    print("🚗 Parking API Server Started")
    print("Admin: admin / admin123")
    print("API docs: http://localhost:8000/docs")
    print("=" * 50)
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)