"""
Amazon Cookie Gen — Bot de Telegram
Compatible con Python 3.14.3
Usando Aiogram 3.x
"""

import os
import sys
import json
import time
import html
import logging
import asyncio
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
from http.server import BaseHTTPRequestHandler, HTTPServer

from dotenv import load_dotenv
load_dotenv()

# Configuración de logging
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("amzbot")

# =============================================================================
# CONFIGURACIÓN
# =============================================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CREDITS_PER_GEN = int(os.getenv("CREDITS_PER_GEN", "15"))
DB_FILE = os.getenv("DB_FILE", "bot_db.json")
ADMIN_DISPLAY_NAME = os.getenv("ADMIN_DISPLAY_NAME", "BILLY")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
PORT = int(os.getenv("PORT", "8080"))

# Loop principal de asyncio para enviar mensajes desde hilos
MAIN_LOOP = None

# =============================================================================
# IMPORTAR AIOGRAM
# =============================================================================

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# Crear bot y dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# =============================================================================
# HEALTH CHECK SERVER
# =============================================================================

def start_health_server():
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            return

    try:
        server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
        logger.info(f"✅ Health server escuchando en 0.0.0.0:{PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {e}")

# =============================================================================
# BASE DE DATOS
# =============================================================================

class DB:
    _lock = threading.Lock()

    def __init__(self, filepath: str):
        self.path = Path(filepath)
        self._data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"users": {}, "generated": [], "credit_log": []}

    def _save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get_user(self, user_id: int) -> Optional[dict]:
        return self._data["users"].get(str(user_id))

    def upsert_user(self, user_id: int, username: str, first_name: str) -> dict:
        with self._lock:
            uid = str(user_id)
            if uid not in self._data["users"]:
                self._data["users"][uid] = {
                    "id": user_id,
                    "username": username or "",
                    "first_name": first_name or "",
                    "credits": 0,
                    "total_generated": 0,
                    "total_credits_received": 0,
                    "joined_at": datetime.now().isoformat(),
                    "last_gen": None,
                }
            else:
                self._data["users"][uid]["username"] = username or self._data["users"][uid].get("username", "")
                self._data["users"][uid]["first_name"] = first_name or self._data["users"][uid].get("first_name", "")
            self._save()
            return self._data["users"][uid]

    def get_credits(self, user_id: int) -> int:
        u = self.get_user(user_id)
        return u["credits"] if u else 0

    def add_credits(self, user_id: int, amount: int, admin_id: int, action: str = "dar") -> int:
        with self._lock:
            uid = str(user_id)
            if uid not in self._data["users"]:
                return -1
            self._data["users"][uid]["credits"] += amount
            self._data["users"][uid]["total_credits_received"] += max(0, amount)
            self._data["credit_log"].append({
                "from_id": admin_id,
                "to_id": user_id,
                "amount": amount,
                "action": action,
                "timestamp": datetime.now().isoformat(),
            })
            self._save()
            return self._data["users"][uid]["credits"]

    def deduct_credits(self, user_id: int, amount: int) -> bool:
        with self._lock:
            uid = str(user_id)
            if uid not in self._data["users"]:
                return False
            if self._data["users"][uid]["credits"] < amount:
                return False
            self._data["users"][uid]["credits"] -= amount
            self._save()
            return True

    def record_gen(self, user_id: int, username: str, phone: str, name: str):
        with self._lock:
            uid = str(user_id)
            if uid in self._data["users"]:
                self._data["users"][uid]["total_generated"] += 1
                self._data["users"][uid]["last_gen"] = datetime.now().isoformat()
            self._data["generated"].append({
                "user_id": user_id,
                "username": username or "",
                "phone": phone,
                "name": name,
                "timestamp": datetime.now().isoformat(),
                "credits_used": CREDITS_PER_GEN,
            })
            self._save()

    def get_all_users(self) -> list[dict]:
        return list(self._data["users"].values())

    def get_stats(self) -> dict:
        users = self._data["users"].values()
        generated = self._data["generated"]
        return {
            "total_users": len(users),
            "total_generated": len(generated),
            "total_credits": sum(u.get("credits", 0) for u in users),
            "active_users": sum(1 for u in users if u.get("total_generated", 0) > 0),
        }

    def find_user_by_username(self, username: str) -> Optional[dict]:
        username = username.lstrip("@").lower()
        for u in self._data["users"].values():
            if (u.get("username") or "").lower() == username:
                return u
        return None


db = DB(DB_FILE)

# =============================================================================
# CONFIGURACIÓN DE GENERACIÓN PARALELA
# =============================================================================

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
_active_futures: Dict[int, concurrent.futures.Future] = {}
_futures_lock = threading.Lock()


def run_create_account_isolated(user_id: int) -> dict:
    try:
        import main as main_module
        creator = main_module.AmazonCreator()
        result = creator.create_account()
        if isinstance(result, dict) and "phone" in result:
            return result
        return {"error": "Resultado inválido de create_account"}
    except Exception as e:
        logger.error(f"[User {user_id}] Error en generación: {e}", exc_info=True)
        return {"error": str(e)}


def get_active_generations_count() -> int:
    with _futures_lock:
        active = 0
        for future in _active_futures.values():
            if not future.done():
                active += 1
        return active


# =============================================================================
# FUNCIONES DE UTILIDAD
# =============================================================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def credits_bar(credits: int, max_credits: int = 100) -> str:
    pct = min(credits, max_credits) / max_credits if max_credits > 0 else 0
    filled = int(pct * 10)
    bar = "█" * filled + "░" * (10 - filled)
    return f"[{bar}] {credits}"


def count_cookies(cookie_string: str) -> int:
    if not cookie_string:
        return 0
    return len([c for c in cookie_string.split(";") if c.strip() and "=" in c])


def split_text(text: str, max_len: int = 4000) -> list:
    if not text:
        return []
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


# =============================================================================
# TECLADOS
# =============================================================================

def get_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 AMAZON", callback_data="menu_amazon")],
        [InlineKeyboardButton(text="💰 MIS CRÉDITOS", callback_data="menu_creditos")],
    ])


def get_confirm_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Confirmar", callback_data=f"gen_confirm:{user_id}"),
            InlineKeyboardButton(text="❌ Cancelar", callback_data=f"gen_cancel:{user_id}"),
        ]
    ])


# =============================================================================
# FUNCIONES DE ENVÍO ASÍNCRONAS
# =============================================================================

async def send_success_message_async(chat_id: int, phone: str, password: str, name: str, cookies: str, elapsed: float):
    safe_phone = html.escape(phone)
    safe_password = html.escape(password)
    safe_name = html.escape(name)

    cookie_count = count_cookies(cookies)

    header = (
        f"⚡ <b>✅ CUENTA GENERADA EXITOSAMENTE</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Teléfono:</b> <code>{safe_phone}</code>\n"
        f"🔑 <b>Contraseña:</b> <code>{safe_password}</code>\n"
        f"👤 <b>Nombre:</b> {safe_name}\n"
        f"🌍 <b>País:</b> USA\n"
        f"🍪 <b>Cookies:</b> {cookie_count}\n"
        f"⏱ <b>Tiempo:</b> <code>{elapsed:.2f}s</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    await bot.send_message(chat_id, header, reply_markup=get_main_keyboard())

    safe_cookies = html.escape(cookies)
    if len(safe_cookies) > 4000:
        chunks = split_text(safe_cookies, 3500)
        for idx, chunk in enumerate(chunks, 1):
            msg = f"🍪 <b>Cookies ({idx}/{len(chunks)}):</b>\n\n<code>{chunk}</code>"
            await bot.send_message(chat_id, msg)
    else:
        await bot.send_message(chat_id, f"🍪 <b>Cookies:</b>\n\n<code>{safe_cookies}</code>")


async def send_error_message_async(chat_id: int, error_msg: str, credits_restored: int):
    await bot.send_message(
        chat_id,
        f"❌ <b>Error al generar la cuenta</b>\n\n"
        f"<code>{html.escape(error_msg[:300])}</code>\n\n"
        f"💳 <b>Tus créditos han sido devueltos</b>\n"
        f"Balance actual: <b>{credits_restored}</b>",
        reply_markup=get_main_keyboard()
    )


async def notify_admin_async(admin_id: int, user, phone: str, name: str, cookie_count: int, elapsed: float):
    await bot.send_message(
        admin_id,
        f"🔔 <b>Nueva generación exitosa</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 {html.escape(user.first_name or '')} (@{user.username or '?'})\n"
        f"📞 {html.escape(phone)}\n"
        f"👤 {html.escape(name)}\n"
        f"🍪 Cookies: {cookie_count}\n"
        f"⏱ Tiempo: {elapsed:.2f}s"
    )


# =============================================================================
# MANEJADORES DE COMANDOS
# =============================================================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    db.upsert_user(user.id, user.username or "", user.first_name or "")
    credits = db.get_credits(user.id)
    active_gens = get_active_generations_count()

    if is_admin(user.id):
        welcome = f"👑 Bienvenido Administrador <b>{html.escape(ADMIN_DISPLAY_NAME)}</b>"
    else:
        welcome = f"👤 Bienvenido <b>{html.escape(user.first_name or 'Usuario')}</b>"

    text = (
        f"⚡ <b>Cookie Gen Amazon</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{welcome}\n"
        f"💰 Créditos disponibles: <b>{credits}</b>\n"
        f"🔄 Generaciones activas: <b>{active_gens}/{MAX_WORKERS}</b>\n\n"
        f"📋 Selecciona una opción:"
    )

    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await cmd_start(message)


@dp.message(Command("creditos"))
async def cmd_creditos(message: types.Message):
    user = message.from_user
    db.upsert_user(user.id, user.username or "", user.first_name or "")
    credits = db.get_credits(user.id)
    user_data = db.get_user(user.id)
    gen = user_data.get("total_generated", 0) if user_data else 0

    text = (
        f"💰 <b>Tus créditos</b>\n\n"
        f"Balance: <b>{credits}</b> créditos\n"
        f"{credits_bar(credits)}\n\n"
        f"🔄 Generaciones realizadas: <b>{gen}</b>\n"
        f"💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>\n"
        f"🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces más"
    )

    await message.answer(text, reply_markup=get_main_keyboard())


@dp.message(Command("gen"))
async def cmd_gen(message: types.Message):
    user = message.from_user
    db.upsert_user(user.id, user.username or "", user.first_name or "")

    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        needed = CREDITS_PER_GEN - credits
        await message.answer(
            f"❌ <b>Créditos insuficientes</b>\n\n"
            f"💳 Tienes: <b>{credits}</b>\n"
            f"💰 Necesitas: <b>{CREDITS_PER_GEN}</b>\n"
            f"📉 Faltan: <b>{needed}</b>",
            reply_markup=get_main_keyboard()
        )
        return

    active_gens = get_active_generations_count()
    if active_gens >= MAX_WORKERS:
        await message.answer(
            f"⏳ <b>Sistema ocupado</b>\n\n"
            f"Actualmente hay <b>{active_gens}/{MAX_WORKERS}</b> generaciones en curso.\n"
            f"Por favor espera.",
            reply_markup=get_main_keyboard()
        )
        return

    await message.answer(
        f"🛒 <b>Generar cuenta Amazon</b>\n\n"
        f"💳 Tus créditos: <b>{credits}</b>\n"
        f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
        f"💳 Quedarás con: <b>{credits - CREDITS_PER_GEN}</b>\n\n"
        f"¿Confirmas?",
        reply_markup=get_confirm_keyboard(user.id)
    )


@dp.message(Command("dar"))
async def cmd_dar(message: types.Message):
    user = message.from_user

    if not is_admin(user.id):
        await message.answer("⛔ Solo administradores pueden dar créditos.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("📝 Uso: /dar @usuario <cantidad>")
        return

    target_arg = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ La cantidad debe ser un número.")
        return

    if amount <= 0:
        await message.answer("❌ La cantidad debe ser positiva.")
        return

    target = db.find_user_by_username(target_arg)
    if not target:
        await message.answer(f"❌ Usuario <code>{html.escape(target_arg)}</code> no encontrado.")
        return

    new_balance = db.add_credits(target["id"], amount, user.id, "dar")
    username = f"@{target['username']}" if target.get("username") else f"ID:{target['id']}"

    await message.answer(
        f"✅ <b>Créditos otorgados</b>\n\n"
        f"👤 Usuario: <b>{html.escape(target['first_name'])}</b> ({html.escape(username)})\n"
        f"➕ Agregados: <b>{amount}</b> créditos\n"
        f"💳 Balance nuevo: <b>{new_balance}</b> créditos"
    )

    try:
        await bot.send_message(
            target["id"],
            f"💰 <b>¡Recibiste créditos!</b>\n\n"
            f"➕ <b>+{amount}</b> créditos\n"
            f"💳 Tu balance: <b>{new_balance}</b>\n\n"
            f"Usa /gen para generar una cuenta Amazon"
        )
    except Exception:
        pass


@dp.message(Command("quitar"))
async def cmd_quitar(message: types.Message):
    user = message.from_user

    if not is_admin(user.id):
        await message.answer("⛔ Solo administradores pueden quitar créditos.")
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("📝 Uso: /quitar @usuario <cantidad>")
        return

    target_arg = args[1].lstrip("@")
    try:
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ La cantidad debe ser un número.")
        return

    if amount <= 0:
        await message.answer("❌ La cantidad debe ser positiva.")
        return

    target = db.find_user_by_username(target_arg)
    if not target:
        await message.answer(f"❌ Usuario <code>{html.escape(target_arg)}</code> no encontrado.")
        return

    new_balance = db.add_credits(target["id"], -amount, user.id, "quitar")
    username = f"@{target['username']}" if target.get("username") else f"ID:{target['id']}"

    await message.answer(
        f"✅ <b>Créditos removidos</b>\n\n"
        f"👤 Usuario: <b>{html.escape(target['first_name'])}</b> ({html.escape(username)})\n"
        f"➖ Removidos: <b>{amount}</b> créditos\n"
        f"💳 Balance nuevo: <b>{new_balance}</b> créditos"
    )


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Solo administradores.")
        return

    s = db.get_stats()
    active_gens = get_active_generations_count()

    text = (
        f"📊 <b>Estadísticas del Bot</b>\n\n"
        f"👥 Usuarios registrados: <b>{s['total_users']}</b>\n"
        f"✅ Usuarios activos: <b>{s['active_users']}</b>\n"
        f"🔄 Total generaciones: <b>{s['total_generated']}</b>\n"
        f"💳 Créditos en circulación: <b>{s['total_credits']}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Generaciones simultáneas máx: <b>{MAX_WORKERS}</b>\n"
        f"🔄 Generaciones activas ahora: <b>{active_gens}</b>\n"
        f"💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>"
    )
    await message.answer(text)


@dp.message(Command("usuarios"))
async def cmd_usuarios(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.answer("⛔ Solo administradores.")
        return

    users = db.get_all_users()
    if not users:
        await message.answer("📭 No hay usuarios registrados.")
        return

    users.sort(key=lambda u: u.get("credits", 0), reverse=True)

    lines = ["👥 <b>Usuarios registrados</b>\n"]
    for u in users[:30]:
        username = f"@{u['username']}" if u.get("username") else f"ID:{u['id']}"
        lines.append(
            f"• <b>{html.escape(u['first_name'])}</b> ({html.escape(username)})\n"
            f"  💳 {u.get('credits', 0)} créditos | 🔄 {u.get('total_generated', 0)} gens"
        )

    if len(users) > 30:
        lines.append(f"\n... y {len(users) - 30} más")

    await message.answer("\n".join(lines))


# =============================================================================
# CALLBACKS
# =============================================================================

@dp.callback_query(lambda c: c.data.startswith("menu_"))
async def callback_menu(callback: CallbackQuery):
    user = callback.from_user
    data = callback.data

    await callback.answer()

    db.upsert_user(user.id, user.username or "", user.first_name or "")

    if data == "menu_creditos":
        credits = db.get_credits(user.id)
        user_data = db.get_user(user.id)
        gen = user_data.get("total_generated", 0) if user_data else 0

        text = (
            f"💰 <b>Tus créditos</b>\n\n"
            f"Balance: <b>{credits}</b> créditos\n"
            f"{credits_bar(credits)}\n\n"
            f"🔄 Generaciones realizadas: <b>{gen}</b>\n"
            f"💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>\n"
            f"🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces más"
        )
        await callback.message.edit_text(text, reply_markup=get_main_keyboard())

    elif data == "menu_amazon":
        credits = db.get_credits(user.id)
        active_gens = get_active_generations_count()

        if credits < CREDITS_PER_GEN:
            needed = CREDITS_PER_GEN - credits
            await callback.message.edit_text(
                f"❌ <b>Créditos insuficientes</b>\n\n"
                f"💳 Tienes: <b>{credits}</b>\n"
                f"💰 Necesitas: <b>{CREDITS_PER_GEN}</b>\n"
                f"📉 Faltan: <b>{needed}</b>",
                reply_markup=get_main_keyboard()
            )
            return

        if active_gens >= MAX_WORKERS:
            await callback.message.edit_text(
                f"⏳ <b>Sistema ocupado</b>\n\n"
                f"Actualmente hay <b>{active_gens}/{MAX_WORKERS}</b> generaciones en curso.",
                reply_markup=get_main_keyboard()
            )
            return

        await callback.message.edit_text(
            f"🛒 <b>Generar cuenta Amazon</b>\n\n"
            f"💳 Tus créditos: <b>{credits}</b>\n"
            f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
            f"💳 Quedarás con: <b>{credits - CREDITS_PER_GEN}</b>\n\n"
            f"¿Confirmas?",
            reply_markup=get_confirm_keyboard(user.id)
        )


@dp.callback_query(lambda c: c.data.startswith("gen_"))
async def callback_gen(callback: CallbackQuery):
    user = callback.from_user
    data = callback.data

    await callback.answer()

    if ":" in data:
        action, uid_str = data.rsplit(":", 1)
        if uid_str.isdigit() and int(uid_str) != user.id:
            await callback.answer("⚠️ Este botón no es tuyo.", show_alert=True)
            return
    else:
        action = data

    if action == "gen_cancel":
        await callback.message.edit_text("❌ Generación cancelada.", reply_markup=get_main_keyboard())
        return

    if action != "gen_confirm":
        return

    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        await callback.message.edit_text("❌ Créditos insuficientes.", reply_markup=get_main_keyboard())
        return

    with _futures_lock:
        if user.id in _active_futures and not _active_futures[user.id].done():
            await callback.message.edit_text("⏳ Ya tienes una generación en curso.", reply_markup=get_main_keyboard())
            return

    if get_active_generations_count() >= MAX_WORKERS:
        await callback.message.edit_text("⏳ Sistema lleno. Intenta más tarde.", reply_markup=get_main_keyboard())
        return

    if not db.deduct_credits(user.id, CREDITS_PER_GEN):
        await callback.message.edit_text("❌ Error al descontar créditos.", reply_markup=get_main_keyboard())
        return

    credits_left = db.get_credits(user.id)

    await callback.message.edit_text(
        f"⏳ <b>Generando cuenta Amazon...</b>\n\n"
        f"💰 Créditos descontados: <b>{CREDITS_PER_GEN}</b>\n"
        f"💳 Balance restante: <b>{credits_left}</b>\n\n"
        f"✅ Te notificaré cuando termine.",
    )

    future = _executor.submit(run_create_account_isolated, user.id)

    with _futures_lock:
        _active_futures[user.id] = future

    def check_result():
        try:
            result = future.result(timeout=300)

            if "error" not in result:
                phone = result.get("phone", "")
                password = result.get("password", "")
                name = result.get("name", "")
                cookies = result.get("cookies", "")
                elapsed = result.get("elapsed", 0)

                db.record_gen(user.id, user.username or "", phone, name)
                cookie_count = count_cookies(cookies)

                asyncio.run_coroutine_threadsafe(
                    send_success_message_async(user.id, phone, password, name, cookies, elapsed),
                    MAIN_LOOP
                ).result()

                for admin_id in ADMIN_IDS:
                    asyncio.run_coroutine_threadsafe(
                        notify_admin_async(admin_id, user, phone, name, cookie_count, elapsed),
                        MAIN_LOOP
                    ).result()

            else:
                db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
                credits_restored = db.get_credits(user.id)

                asyncio.run_coroutine_threadsafe(
                    send_error_message_async(user.id, result["error"], credits_restored),
                    MAIN_LOOP
                ).result()

        except concurrent.futures.TimeoutError:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            credits_restored = db.get_credits(user.id)

            asyncio.run_coroutine_threadsafe(
                send_error_message_async(user.id, "Timeout de 5 minutos excedido", credits_restored),
                MAIN_LOOP
            ).result()

        except Exception as e:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            credits_restored = db.get_credits(user.id)
            logger.error(f"Error procesando resultado: {e}", exc_info=True)

            try:
                asyncio.run_coroutine_threadsafe(
                    send_error_message_async(user.id, str(e), credits_restored),
                    MAIN_LOOP
                ).result()
            except Exception:
                pass

        finally:
            with _futures_lock:
                _active_futures.pop(user.id, None)

    threading.Thread(target=check_result, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

async def main():
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()

    if not BOT_TOKEN:
        print("❌ BOT_TOKEN no configurado.")
        sys.exit(1)

    print("🤖 Iniciando bot de Amazon Cookie Gen...")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"   📊 CONFIGURACIÓN:")
    print(f"   ├─ ADMIN_IDS:       {ADMIN_IDS}")
    print(f"   ├─ CREDITS_PER_GEN: {CREDITS_PER_GEN}")
    print(f"   ├─ MAX_WORKERS:     {MAX_WORKERS}")
    print(f"   ├─ PORT:            {PORT}")
    print(f"   └─ DB_FILE:         {DB_FILE}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print("✅ Health server iniciado")

    print("✅ Bot iniciado correctamente. Esperando mensajes...")
    await dp.start_polling(bot)


def start():
    asyncio.run(main())


if __name__ == "__main__":
    start()