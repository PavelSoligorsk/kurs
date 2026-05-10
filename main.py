from fastapi import FastAPI, HTTPException, Depends, status, Cookie, Cookie, Header, UploadFile, Form, File, WebSocket, WebSocketDisconnect
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
import logging  # ← ДОБАВИТЬ ЭТОТ ИМПОРТ
import asyncio
import json
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

# Настройка логгера
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)  # ← СОЗДАТЬ ЛОГГЕР

from utils.number_plate_recognizer import plate_recognizer  # ← исправлено: импортируем экземпляр, а не функцию


SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "your-email@gmail.com")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "your-app-password")
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "noreply@parking.com")

app = FastAPI(title="Barrier Live Parking API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
        expose_headers=["*"],  # ← Добавить эту строку
)

# Подключаем статические файлы
app.mount("/static", StaticFiles(directory="static"), name="static")

# ==================== Конфигурация ====================
DB_PATH = "parking.db"
SESSION_EXPIRE_MINUTES = 60

# Хранилище сессий
sessions: Dict[str, dict] = {}
# WebSocket соединения от шлагбаумов
gate_connections: Dict[str, WebSocket] = {}
gate_lock = threading.Lock()

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

@app.websocket("/ws/gate/{gate_id}")
async def websocket_gate_endpoint(websocket: WebSocket, gate_id: str):
    # Принимаем соединение от любого источника
    await websocket.accept()  # Без проверки origin
    
    with gate_lock:
        gate_connections[gate_id] = websocket
    
    logger.info(f"✅ Шлагбаум {gate_id} подключён через WebSocket")
    
    await websocket.send_json({"status": "connected", "gate_id": gate_id})
    
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            logger.info(f"📩 От шлагбаума {gate_id}: {msg}")
    except WebSocketDisconnect:
        logger.info(f"❌ Шлагбаум {gate_id} отключился")
    except Exception as e:
        logger.error(f"⚠️ Ошибка WebSocket: {e}")
    finally:
        with gate_lock:
            gate_connections.pop(gate_id, None)

async def send_gate_command(gate_id: str, command: str, params: dict = None) -> bool:
    """Отправить команду на шлагбаум и дождаться подтверждения"""
    with gate_lock:
        ws = gate_connections.get(gate_id)
    
    if not ws:
        logger.warning(f"⚠️ Шлагбаум {gate_id} не подключён")
        return False
    
    try:
        message = {"command": command}
        if params:
            message.update(params)
        
        await ws.send_json(message)
        
        # Ждём подтверждение
        response = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        response_data = json.loads(response)
        
        if response_data.get("status") == "ok":
            logger.info(f"✅ Команда {command} выполнена на {gate_id}")
            return True
        else:
            logger.error(f"❌ Ошибка выполнения {command}: {response_data}")
            return False
            
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Таймаут ожидания ответа от {gate_id}")
        return False
    except Exception as e:
        logger.error(f"⚠️ Ошибка отправки команды: {e}")
        return False

async def send_gate_command(gate_id: str, command: str, params: dict = None) -> bool:
    """Отправить команду на шлагбаум и дождаться подтверждения"""
    with gate_lock:
        ws = gate_connections.get(gate_id)
    
    if not ws:
        logger.warning(f"⚠️ Шлагбаум {gate_id} не подключён")
        return False
    
    try:
        message = {"command": command}
        if params:
            message.update(params)
        
        await ws.send_json(message)
        
        # Ждём подтверждение
        response = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
        response_data = json.loads(response)
        
        if response_data.get("status") == "ok":
            logger.info(f"✅ Команда {command} выполнена на {gate_id}")
            return True
        else:
            logger.error(f"❌ Ошибка выполнения {command}: {response_data}")
            return False
            
    except asyncio.TimeoutError:
        logger.error(f"⏱️ Таймаут ожидания ответа от {gate_id}")
        return False
    except Exception as e:
        logger.error(f"⚠️ Ошибка отправки команды: {e}")
        return False

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

def send_email(to_email: str, subject: str, body: str):
    """Отправка email-уведомления"""
    try:
        msg = MIMEMultipart()
        msg["From"] = SENDER_EMAIL
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(msg)
            
        print(f"Email отправлен на {to_email}")
        return True
    except Exception as e:
        print(f"Ошибка отправки email: {e}")
        # Не прерываем выполнение основной функции при ошибке отправки
        return False

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

@app.post("/api/camera/process-plate")
async def process_plate_from_camera(
    image: UploadFile = File(...),
    camera_id: str = Form(default="main_gate"),
    direction: str = Form(default="auto"),  # auto, enter, exit
    fast_mode: bool = Form(default=True)  # быстрый режим
):
    """
    Принимает изображение с камеры, распознает номер,
    проверяет права доступа и возвращает решение.
    
    Возвращает:
    - approved: true/false - разрешен ли проезд
    - confidence: 0.0-1.0 - уверенность распознавания
    - action: open_gate_enter / open_gate_exit / deny / manual_check
    - message: текстовое сообщение
    """
    start_time = datetime.now()
    
    try:
        # Читаем изображение
        contents = await image.read()
        
        # Сохраняем изображение только в debug режиме
        debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
        if debug_mode:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            os.makedirs("captures", exist_ok=True)
            capture_filename = f"captures/{camera_id}_{direction}_{timestamp}.jpg"
            with open(capture_filename, "wb") as f:
                f.write(contents)
        
        # Распознаем номер с оптимизированным распознавателем
        plate_number, confidence = await plate_recognizer.recognize_plate(
            contents
        )
        
        # Логируем результат
        logger.info(f"Recognition result: plate='{plate_number}', confidence={confidence:.2f}, time={datetime.now() - start_time}")
        
        # Если номер не распознан
        if not plate_number or confidence < 0.5:
            return {
                "approved": False,
                "confidence": confidence or 0.0,
                "plate_number": None,
                "action": "manual_check",
                "message": "Номер не распознан. Требуется ручная проверка.",
                "timestamp": datetime.now().isoformat(),
                "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Ищем пользователя по номеру машины (поддерживаем оба формата)
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Пробуем найти номер в разных форматах
        normalized_plate = plate_number.replace("-", " ").replace("BY-", "")
        cursor.execute(
            """SELECT id, email, car_number, is_inside, is_blocked, blocked_until 
               FROM users WHERE car_number = ? OR car_number = ?""",
            (plate_number, normalized_plate)
        )
        user = cursor.fetchone()
        
        # Номер не зарегистрирован
        if not user:
            cursor.close()
            conn.close()
            return {
                "approved": False,
                "confidence": confidence,
                "plate_number": plate_number,
                "action": "deny",
                "message": f"Номер {plate_number} не зарегистрирован в системе.",
                "timestamp": datetime.now().isoformat(),
                "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Проверяем блокировку
        if user["is_blocked"] == 1:
            if user["blocked_until"]:
                blocked_until = datetime.fromisoformat(user["blocked_until"])
                if datetime.now() > blocked_until:
                    # Разблокируем, если время истекло
                    cursor.execute(
                        "UPDATE users SET is_blocked = 0, blocked_until = NULL WHERE id = ?",
                        (user["id"],)
                    )
                    conn.commit()
                else:
                    cursor.close()
                    conn.close()
                    return {
                        "approved": False,
                        "confidence": confidence,
                        "plate_number": plate_number,
                        "action": "deny",
                        "message": f"Пользователь заблокирован до {blocked_until.strftime('%d.%m.%Y %H:%M')}",
                        "timestamp": datetime.now().isoformat(),
                        "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
                    }
            else:
                cursor.close()
                conn.close()
                return {
                    "approved": False,
                    "confidence": confidence,
                    "plate_number": plate_number,
                    "action": "deny",
                    "message": "Пользователь заблокирован.",
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
                }
        
        # Определяем направление движения
        detected_direction = direction
        if direction == "auto":
            # Автоопределение: если машина внутри - выезд, снаружи - въезд
            if user["is_inside"] == 1:
                detected_direction = "exit"
            else:
                detected_direction = "enter"
        
        # Логика въезда
        if detected_direction == "enter":
            if user["is_inside"] == 1:
                cursor.close()
                conn.close()
                return {
                    "approved": False,
                    "confidence": confidence,
                    "plate_number": plate_number,
                    "action": "deny",
                    "message": f"Машина {plate_number} уже на парковке.",
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
                }
            
            # Проверяем свободные места
            cursor.execute("SELECT free_count FROM parking_spots WHERE id = 1")
            spots = cursor.fetchone()
            
            if spots["free_count"] <= 0:
                cursor.close()
                conn.close()
                return {
                    "approved": False,
                    "confidence": confidence,
                    "plate_number": plate_number,
                    "action": "deny",
                    "message": "Нет свободных мест.",
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
                }
            
            # Регистрируем въезд
            cursor.execute("UPDATE users SET is_inside = 1 WHERE id = ?", (user["id"],))
            cursor.execute(
                """UPDATE parking_spots 
                   SET free_count = free_count - 1, 
                       occupied_count = occupied_count + 1 
                   WHERE id = 1"""
            )
            
            # Логируем событие въезда
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "event": "entry",
                "plate": plate_number,
                "user_email": user["email"],
                "confidence": confidence,
                "camera_id": camera_id
            }
            
            # Сохраняем лог в файл (опционально)
            try:
                import json
                os.makedirs("logs", exist_ok=True)
                with open("logs/gate_events.log", "a", encoding="utf-8") as log_file:
                    log_file.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
            except:
                pass
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return {
                "approved": True,
                "confidence": confidence,
                "plate_number": plate_number,
                "action": "open_gate_enter",
                "message": f"Въезд разрешен. Добро пожаловать!",
                "timestamp": datetime.now().isoformat(),
                "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
            }
        
        # Логика выезда
        elif detected_direction == "exit":
            return {
                    "approved": False,
                    "confidence": confidence,
                    "plate_number": plate_number,
                    "action": "deny",
                    "message": f"Машина {plate_number} на парковке. Въезд только по фото.",
                    "timestamp": datetime.now().isoformat(),
                    "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
                }
        
    except Exception as e:
        logger.error(f"Error processing plate: {e}", exc_info=True)
        return {
            "approved": False,
            "confidence": 0.0,
            "plate_number": None,
            "action": "manual_check",
            "message": f"Ошибка: {str(e)}",
            "timestamp": datetime.now().isoformat(),
            "processing_time_ms": (datetime.now() - start_time).total_seconds() * 1000
        }

@app.post("/api/gate/exit")
async def user_exit_gate(current_user: dict = Depends(get_current_user)):
    """Пользователь выезжает (открытие на 7 секунд)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Проверяем статус пользователя
    cursor.execute("SELECT is_inside, is_blocked FROM users WHERE email = ?", (current_user["email"],))
    user = cursor.fetchone()
    
    if not user:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    
    if user["is_blocked"]:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=403, detail="Пользователь заблокирован")
    
    if not user["is_inside"]:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=400, detail="Машина не находится на парковке")
    
    # Отправляем команду open_exit на Raspberry Pi (с автозакрытием 7 сек)
    gate_result = await send_gate_command("main_gate", "open_exit")
    
    if gate_result:
        # Обновляем статус
        cursor.execute("UPDATE users SET is_inside = 0 WHERE email = ?", (current_user["email"],))
        conn.commit()
        cursor.close()
        conn.close()
        
        return {"message": "Шлагбаум открыт, выезд разрешен (7 секунд)"}
    else:
        cursor.close()
        conn.close()
        raise HTTPException(status_code=503, detail="Шлагбаум не отвечает")

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
        # Создаем пользователя
        cursor.execute("""
            INSERT INTO users (email, car_number, passport_number, password_hash, is_inside, is_blocked)
            VALUES (?, ?, ?, ?, 0, 0)
        """, (ticket["email"], ticket["car_number"], ticket["passport_number"], ticket["password_hash"]))
        
        # Обновляем статус заявки
        cursor.execute("UPDATE tickets SET status = 'approved' WHERE id = ?", (ticket_id,))
        conn.commit()
        
        # Отправляем email об одобрении
        email_subject = "Заявка на регистрацию одобрена"
        email_body = f"""Уважаемый пользователь!

Ваша заявка на регистрацию №{ticket_id} была одобрена.

Данные для входа:
Email: {ticket["email"]}
Номер автомобиля: {ticket["car_number"]}

Теперь вы можете войти в систему и пользоваться парковкой.

С уважением,
Администрация парковки"""
        
        send_email(ticket["email"], email_subject, email_body)
        
    except sqlite3.IntegrityError:
        conn.rollback()
        raise HTTPException(status_code=400, detail="Пользователь уже существует")
    finally:
        cursor.close()
        conn.close()
    
    return {"message": f"Заявка {ticket_id} одобрена"}

@app.post("/api/admin/reject-ticket/{ticket_id}")
def admin_reject_ticket(ticket_id: int, _: bool = Depends(verify_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Получаем данные заявки перед обновлением
    cursor.execute("SELECT * FROM tickets WHERE id = ? AND status = 'pending'", (ticket_id,))
    ticket = cursor.fetchone()
    
    if not ticket:
        raise HTTPException(status_code=404, detail="Заявка не найдена")
    
    # Отклоняем заявку
    cursor.execute("UPDATE tickets SET status = 'rejected' WHERE id = ? AND status = 'pending'", (ticket_id,))
    conn.commit()
    
    # Отправляем email об отклонении
    email_subject = "Заявка на регистрацию отклонена"
    email_body = f"""Уважаемый пользователь!

К сожалению, ваша заявка на регистрацию №{ticket_id} была отклонена.

Если вы считаете, что это ошибка, пожалуйста, свяжитесь с администрацией.

С уважением,
Администрация парковки"""
    
    send_email(ticket["email"], email_subject, email_body)
    
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

@app.post("/api/admin/gate/open")
async def admin_open_gate(_: bool = Depends(verify_admin)):
    """Админ вручную открывает шлагбаум (БЕЗ автозакрытия)"""
    # Отправляем команду open_exit_admin на Raspberry Pi
    gate_result = await send_gate_command("main_gate", "open_exit_admin")
    
    if gate_result:
        return {"message": "Шлагбаум открыт администратором (без автозакрытия)"}
    else:
        raise HTTPException(status_code=503, detail="Шлагбаум не отвечает")

@app.post("/api/admin/gate/close")
async def admin_close_gate(_: bool = Depends(verify_admin)):
    """Админ вручную закрывает шлагбаум"""
    gate_result = await send_gate_command("main_gate", "close")
    
    if gate_result:
        return {"message": "Шлагбаум закрыт администратором"}
    else:
        raise HTTPException(status_code=503, detail="Шлагбаум не отвечает")

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