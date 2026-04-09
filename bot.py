"""
Amazon Cookie Gen — Bot de Telegram
=====================================
"""

import os
import sys
import re
import json
import time
import html
import logging
import asyncio
import threading
import concurrent.futures
from pathlib import Path
from datetime import datetime
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    Updater,
)
from telegram.constants import ParseMode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CREDITS_PER_GEN = int(os.getenv("CREDITS_PER_GEN", "15"))
DB_FILE = os.getenv("DB_FILE", "bot_db.json")
ADMIN_DISPLAY_NAME = os.getenv("ADMIN_DISPLAY_NAME", "BILLY")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
PORT = int(os.getenv("PORT", "8080"))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("amzbot")

# Parche para Python 3.14 - evitar el error de __polling_cleanup_cb
if sys.version_info >= (3, 14):
    # Parchear la clase Updater para que funcione con Python 3.14
    original_init = Updater.__init__
    
    def patched_init(self, *args, **kwargs):
        # Llamar al original
        original_init(self, *args, **kwargs)
        # Asegurar que el atributo existe
        if not hasattr(self, '_Updater__polling_cleanup_cb'):
            object.__setattr__(self, '_Updater__polling_cleanup_cb', None)
    
    Updater.__init__ = patched_init


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
            "total_credits": sum(u["credits"] for u in users),
            "active_users": sum(1 for u in users if u["total_generated"] > 0),
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
_active_futures: dict[int, concurrent.futures.Future] = {}
_futures_lock = threading.Lock()
_event_loop: asyncio.AbstractEventLoop = None


def run_create_account_isolated(user_id: int) -> dict:
    try:
        import main as main_module
        
        creator = main_module.AmazonCreator()
        result = creator.create_account()
        
        if isinstance(result, dict) and "phone" in result:
            return result
        else:
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


def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛒 AMAZON", callback_data="menu_amazon")],
        [InlineKeyboardButton("💰 MIS créditos", callback_data="menu_creditos")],
    ])


def build_confirm_menu(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"gen_confirm:{user_id}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"gen_cancel:{user_id}"),
        ]
    ])


def count_cookies(cookie_string: str) -> int:
    return len([c for c in cookie_string.split(";") if c.strip() and "=" in c])


# =============================================================================
# FUNCIONES DE ENVÍO
# =============================================================================

async def send_gen_success_async(app, user_id: int, phone: str, password: str, name: str, cookies: str, cookie_count: int, elapsed: float):
    safe_phone = html.escape(phone)
    safe_password = html.escape(password)
    safe_name = html.escape(name)
    safe_cookies = html.escape(cookies)
    
    header_text = (
        f"⚡ <b>✅ CUENTA GENERADA EXITOSAMENTE</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Teléfono:</b> <code>{safe_phone}</code>\n"
        f"🔑 <b>Contraseña:</b> <code>{safe_password}</code>\n"
        f"👤 <b>Nombre:</b> {safe_name}\n"
        f"🌍 <b>País:</b> USA\n"
        f"🍪 <b>Cookies:</b> {cookie_count}\n"
        f"⏱ <b>Tiempo:</b> <code>{elapsed:.2f} segundos</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    cookie_msg = f"🍪 <b>COOKIES COMPLETAS:</b>\n\n<code>{safe_cookies}</code>"
    
    try:
        await app.bot.send_message(user_id, header_text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())
        
        if len(cookie_msg) > 4096:
            chunks = [safe_cookies[i:i+3500] for i in range(0, len(safe_cookies), 3500)]
            for idx, chunk in enumerate(chunks, start=1):
                await app.bot.send_message(user_id, f"🍪 <b>Cookies ({idx}/{len(chunks)}):</b>\n\n<code>{chunk}</code>", parse_mode=ParseMode.HTML)
        else:
            await app.bot.send_message(user_id, cookie_msg, parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error enviando mensaje a {user_id}: {e}")


async def send_gen_error_async(app, user_id: int, error_msg: str, credits_restored: int):
    try:
        await app.bot.send_message(
            user_id,
            f"❌ <b>Error al generar la cuenta</b>\n\n<code>{html.escape(error_msg[:300])}</code>\n\n💳 <b>Tus créditos han sido devueltos</b>\nBalance actual: <b>{credits_restored}</b>\n\n🔄 Intenta nuevamente con /gen",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
    except Exception as e:
        logger.error(f"Error enviando error a {user_id}: {e}")


async def notify_admin_success_async(app, admin_id: int, user, phone: str, name: str, cookie_count: int, elapsed: float):
    username_tag = f"@{user.username}" if user.username else f"ID:{user.id}"
    try:
        await app.bot.send_message(
            admin_id,
            f"🔔 <b>Nueva generación exitosa</b>\n━━━━━━━━━━━━━━━━━━━━━━\n👤 {html.escape(user.first_name or '')} ({html.escape(username_tag)})\n📞 {html.escape(phone)}\n👤 {html.escape(name)}\n🍪 Cookies: {cookie_count}\n⏱ Tiempo: {elapsed:.2f}s",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def notify_admin_error_async(app, admin_id: int, user, error_msg: str):
    username_tag = f"@{user.username}" if user.username else f"ID:{user.id}"
    try:
        await app.bot.send_message(
            admin_id,
            f"⚠️ <b>Error en generación</b>\n━━━━━━━━━━━━━━━━━━━━━━\n👤 {html.escape(user.first_name or '')} ({html.escape(username_tag)})\n❌ Error: <code>{html.escape(error_msg[:200])}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# =============================================================================
# MANEJADORES DE COMANDOS
# =============================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    credits = db.get_credits(user.id)
    active_gens = get_active_generations_count()

    if is_admin(user.id):
        welcome = f"👑 Bienvenido Administrador <b>{html.escape(ADMIN_DISPLAY_NAME)}</b>"
    else:
        welcome = f"👤 Bienvenido <b>{html.escape(user.first_name or 'Usuario')}</b>"

    text = f"⚡ <b>Cookie Gen Amazon</b> ⚡\n━━━━━━━━━━━━━━━━━\n\n{welcome}\n💰 Créditos disponibles: <b>{credits}</b>\n🔄 Generaciones activas: <b>{active_gens}/{MAX_WORKERS}</b>\n\n📋 Selecciona una opción:"
    await ctx.bot.send_message(update.effective_chat.id, text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, ctx)


async def cmd_creditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    credits = db.get_credits(user.id)
    gen = db.get_user(user.id)["total_generated"] if db.get_user(user.id) else 0

    text = f"💰 <b>Tus créditos</b>\n\nBalance: <b>{credits}</b> créditos\n{credits_bar(credits)}\n\n🔄 Generaciones realizadas: <b>{gen}</b>\n💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>\n🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces más"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())


async def cmd_dar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    # Implementación simplificada - puedes agregar la lógica completa después
    await update.message.reply_text("✅ Comando dar - Admin")


async def cmd_quitar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    await update.message.reply_text("✅ Comando quitar - Admin")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    s = db.get_stats()
    text = f"📊 <b>Estadísticas</b>\n\n👥 Usuarios: {s['total_users']}\n🔄 Generaciones: {s['total_generated']}\n💳 Créditos: {s['total_credits']}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_usuarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    users = db.get_all_users()
    if not users:
        await update.message.reply_text("No hay usuarios.")
        return
    text = "👥 Usuarios:\n" + "\n".join([f"• {u['first_name']} - {u['credits']} créditos" for u in users[:10]])
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    
    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        await update.message.reply_text(f"❌ Créditos insuficientes. Necesitas {CREDITS_PER_GEN}.", reply_markup=build_main_menu())
        return
    
    await update.message.reply_text(
        f"🛒 <b>Generar cuenta Amazon</b>\n\n💳 Tus créditos: {credits}\n💰 Costo: {CREDITS_PER_GEN}\n💳 Quedarás con: {credits - CREDITS_PER_GEN}\n\n¿Confirmas?",
        parse_mode=ParseMode.HTML,
        reply_markup=build_confirm_menu(user.id),
    )


async def callback_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    if data == "menu_creditos":
        credits = db.get_credits(user.id)
        await query.edit_message_text(f"💰 Tus créditos: {credits}", reply_markup=build_main_menu())
    elif data == "menu_amazon":
        credits = db.get_credits(user.id)
        if credits < CREDITS_PER_GEN:
            await query.edit_message_text(f"❌ Créditos insuficientes.", reply_markup=build_main_menu())
        else:
            await query.edit_message_text(
                f"🛒 Generar cuenta\n💳 Créditos: {credits}\n💰 Costo: {CREDITS_PER_GEN}\n¿Confirmas?",
                reply_markup=build_confirm_menu(user.id)
            )


async def callback_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    if ":" in data:
        action, uid_str = data.rsplit(":", 1)
        if uid_str.isdigit() and int(uid_str) != user.id:
            await query.answer("⚠️ Este botón no es tuyo.", show_alert=True)
            return
    else:
        action = data

    if action == "gen_cancel":
        await query.edit_message_text("❌ Generación cancelada.", reply_markup=build_main_menu())
        return

    if action != "gen_confirm":
        return

    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        await query.edit_message_text("❌ Créditos insuficientes.", reply_markup=build_main_menu())
        return

    if not db.deduct_credits(user.id, CREDITS_PER_GEN):
        await query.edit_message_text("❌ Error al descontar créditos.", reply_markup=build_main_menu())
        return

    await query.edit_message_text("⏳ Generando cuenta... Te notificaré cuando termine.", parse_mode=ParseMode.HTML)
    
    app = ctx.application
    loop = _event_loop
    future = _executor.submit(run_create_account_isolated, user.id)
    
    with _futures_lock:
        _active_futures[user.id] = future

    def check_result():
        try:
            result = future.result(timeout=300)
            if "error" not in result:
                db.record_gen(user.id, user.username or "", result["phone"], result["name"])
                asyncio.run_coroutine_threadsafe(
                    send_gen_success_async(app, user.id, result["phone"], result["password"], result["name"], result["cookies"], count_cookies(result["cookies"]), result["elapsed"]),
                    loop
                )
            else:
                db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
                asyncio.run_coroutine_threadsafe(send_gen_error_async(app, user.id, result["error"], db.get_credits(user.id)), loop)
        except Exception as e:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            asyncio.run_coroutine_threadsafe(send_gen_error_async(app, user.id, str(e), db.get_credits(user.id)), loop)
        finally:
            with _futures_lock:
                _active_futures.pop(user.id, None)

    threading.Thread(target=check_result, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

def main():
    global _event_loop
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN no configurado.")
        sys.exit(1)

    print("🤖 Iniciando bot de Amazon Cookie Gen...")
    print(f"   ADMIN_IDS: {ADMIN_IDS}")
    print(f"   MAX_WORKERS: {MAX_WORKERS}")

    # Iniciar health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    # Crear event loop
    _event_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_event_loop)
    
    # Crear aplicación
    app = Application.builder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("gen", cmd_gen))
    app.add_handler(CommandHandler("creditos", cmd_creditos))
    app.add_handler(CommandHandler("dar", cmd_dar))
    app.add_handler(CommandHandler("quitar", cmd_quitar))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("usuarios", cmd_usuarios))
    app.add_handler(CallbackQueryHandler(callback_menu, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(callback_gen, pattern="^gen_"))

    print("✅ Bot iniciado correctamente.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    CREDITS_PER_GEN = int(os.getenv("CREDITS_PER_GEN", "15"))
    DB_FILE = os.getenv("DB_FILE", "bot_db.json")
    ADMIN_DISPLAY_NAME = os.getenv("ADMIN_DISPLAY_NAME", "BILLY")
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "2"))
    PORT = int(os.getenv("PORT", "8080"))
    
    main()