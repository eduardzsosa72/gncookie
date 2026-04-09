"""
Amazon Cookie Gen — Bot de Telegram
Compatible con Python 3.14
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
from typing import Optional, Dict, Any
from http.server import BaseHTTPRequestHandler, HTTPServer

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

# =============================================================================
# IMPORTAR TELEGRAM CON MANEJO DE ERRORES PARA PYTHON 3.14
# =============================================================================

# Parche para evitar el error de __polling_cleanup_cb en Python 3.14
import types
import telegram.ext as tele_ext

# Guardar el Updater original si existe
_original_updater = None
if hasattr(tele_ext, 'Updater'):
    _original_updater = tele_ext.Updater

# Crear una versión compatible de Updater para Python 3.14
class CompatibleUpdater:
    """Updater compatible con Python 3.14"""
    
    def __init__(self, bot=None, update_queue=None, **kwargs):
        self.bot = bot
        self.update_queue = update_queue
        self._polling_cleanup_cb = None
        self._running = False
        self.logger = logging.getLogger(__name__)
    
    def start_polling(self, *args, **kwargs):
        self._running = True
        return self
    
    def stop(self):
        self._running = False
    
    def is_running(self):
        return self._running


# Reemplazar Updater con la versión compatible
tele_ext.Updater = CompatibleUpdater

# Ahora importar el resto de telegram
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode

# =============================================================================
# HEALTH CHECK SERVER
# =============================================================================

def start_health_server():
    """Servidor HTTP para health checks de Railway"""
    
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
_event_loop: asyncio.AbstractEventLoop = None


def run_create_account_isolated(user_id: int) -> dict:
    """
    Ejecuta create_account() de main.py de forma AISLADA.
    Cada llamada crea su propia instancia de AmazonCreator.
    """
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
    if not cookie_string:
        return 0
    return len([c for c in cookie_string.split(";") if c.strip() and "=" in c])


def split_text(text: str, max_len: int = 4000) -> list[str]:
    """Divide texto en partes seguras para Telegram"""
    if not text:
        return []
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


# =============================================================================
# FUNCIONES DE ENVÍO
# =============================================================================

async def send_success_message(app, user_id: int, phone: str, password: str, name: str, cookies: str, elapsed: float):
    """Envía mensaje de éxito"""
    safe_phone = html.escape(phone)
    safe_password = html.escape(password)
    safe_name = html.escape(name)
    
    cookie_count = count_cookies(cookies)
    
    header = (
        f"⚡ <b>✅ CUENTA GENERADA</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📞 <b>Teléfono:</b> <code>{safe_phone}</code>\n"
        f"🔑 <b>Contraseña:</b> <code>{safe_password}</code>\n"
        f"👤 <b>Nombre:</b> {safe_name}\n"
        f"🌍 <b>País:</b> USA\n"
        f"🍪 <b>Cookies:</b> {cookie_count}\n"
        f"⏱ <b>Tiempo:</b> <code>{elapsed:.2f}s</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    
    await app.bot.send_message(user_id, header, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())
    
    # Enviar cookies
    safe_cookies = html.escape(cookies)
    if len(safe_cookies) > 4000:
        chunks = split_text(safe_cookies, 3500)
        for idx, chunk in enumerate(chunks, 1):
            msg = f"🍪 <b>Cookies ({idx}/{len(chunks)}):</b>\n\n<code>{chunk}</code>"
            await app.bot.send_message(user_id, msg, parse_mode=ParseMode.HTML)
    else:
        await app.bot.send_message(user_id, f"🍪 <b>Cookies:</b>\n\n<code>{safe_cookies}</code>", parse_mode=ParseMode.HTML)


async def send_error_message(app, user_id: int, error_msg: str, credits_restored: int):
    """Envía mensaje de error"""
    await app.bot.send_message(
        user_id,
        f"❌ <b>Error al generar</b>\n\n<code>{html.escape(error_msg[:300])}</code>\n\n💳 Créditos devueltos: <b>{credits_restored}</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


# =============================================================================
# MANEJADORES DE COMANDOS
# =============================================================================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    credits = db.get_credits(user.id)
    active_gens = get_active_generations_count()

    if is_admin(user.id):
        welcome = f"👑 Bienvenido Admin <b>{html.escape(ADMIN_DISPLAY_NAME)}</b>"
    else:
        welcome = f"👤 Bienvenido <b>{html.escape(user.first_name or 'Usuario')}</b>"

    text = (
        f"⚡ <b>Cookie Gen Amazon</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{welcome}\n"
        f"💰 Créditos: <b>{credits}</b>\n"
        f"🔄 Generaciones activas: <b>{active_gens}/{MAX_WORKERS}</b>\n\n"
        f"📋 Selecciona una opción:"
    )
    
    await ctx.bot.send_message(
        update.effective_chat.id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


async def cmd_creditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    credits = db.get_credits(user.id)
    gen = db.get_user(user.id).get("total_generated", 0) if db.get_user(user.id) else 0

    text = (
        f"💰 <b>Tus créditos</b>\n\n"
        f"Balance: <b>{credits}</b>\n"
        f"{credits_bar(credits)}\n\n"
        f"🔄 Generaciones: <b>{gen}</b>\n"
        f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
        f"🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces"
    )
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())


async def cmd_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    
    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        needed = CREDITS_PER_GEN - credits
        await update.message.reply_text(
            f"❌ Créditos insuficientes. Faltan <b>{needed}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
        return
    
    active_gens = get_active_generations_count()
    if active_gens >= MAX_WORKERS:
        await update.message.reply_text(
            f"⏳ Sistema ocupado ({active_gens}/{MAX_WORKERS}). Intenta más tarde.",
            reply_markup=build_main_menu(),
        )
        return
    
    await update.message.reply_text(
        f"🛒 <b>Generar cuenta Amazon</b>\n\n"
        f"💳 Créditos: <b>{credits}</b>\n"
        f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
        f"💳 Quedarás: <b>{credits - CREDITS_PER_GEN}</b>\n\n"
        f"¿Confirmas?",
        parse_mode=ParseMode.HTML,
        reply_markup=build_confirm_menu(user.id),
    )


async def cmd_dar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("📝 Uso: /dar @usuario <cantidad>")
        return
    
    target_arg = args[0].lstrip("@")
    amount = int(args[1])
    
    target = db.find_user_by_username(target_arg)
    if not target:
        await update.message.reply_text(f"❌ Usuario {target_arg} no encontrado.")
        return
    
    new_balance = db.add_credits(target["id"], amount, user.id, "dar")
    await update.message.reply_text(f"✅ {amount} créditos dados a {target['first_name']}. Nuevo balance: {new_balance}")


async def cmd_quitar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    
    args = ctx.args
    if len(args) < 2:
        await update.message.reply_text("📝 Uso: /quitar @usuario <cantidad>")
        return
    
    target_arg = args[0].lstrip("@")
    amount = int(args[1])
    
    target = db.find_user_by_username(target_arg)
    if not target:
        await update.message.reply_text(f"❌ Usuario {target_arg} no encontrado.")
        return
    
    new_balance = db.add_credits(target["id"], -amount, user.id, "quitar")
    await update.message.reply_text(f"✅ {amount} créditos quitados a {target['first_name']}. Nuevo balance: {new_balance}")


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    
    s = db.get_stats()
    active_gens = get_active_generations_count()
    
    text = (
        f"📊 <b>Estadísticas</b>\n\n"
        f"👥 Usuarios: <b>{s['total_users']}</b>\n"
        f"🔄 Generaciones: <b>{s['total_generated']}</b>\n"
        f"💳 Créditos: <b>{s['total_credits']}</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚙️ Max workers: <b>{MAX_WORKERS}</b>\n"
        f"🔄 Activas: <b>{active_gens}</b>\n"
        f"💰 Costo: <b>{CREDITS_PER_GEN}</b>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_usuarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return
    
    users = db.get_all_users()
    if not users:
        await update.message.reply_text("📭 No hay usuarios.")
        return
    
    users.sort(key=lambda u: u.get("credits", 0), reverse=True)
    
    lines = ["👥 <b>Usuarios</b>\n"]
    for u in users[:20]:
        name = html.escape(u.get("first_name", "?"))
        credits = u.get("credits", 0)
        gen = u.get("total_generated", 0)
        lines.append(f"• {name} - 💳 {credits} | 🔄 {gen}")
    
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# =============================================================================
# CALLBACKS
# =============================================================================

async def callback_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    db.upsert_user(user.id, user.username, user.first_name)

    if data == "menu_creditos":
        credits = db.get_credits(user.id)
        gen = db.get_user(user.id).get("total_generated", 0) if db.get_user(user.id) else 0
        text = f"💰 <b>Tus créditos</b>\n\nBalance: <b>{credits}</b>\n{credits_bar(credits)}\n\n🔄 Generaciones: {gen}\n💰 Costo: {CREDITS_PER_GEN}"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=build_main_menu())
    
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
            await query.answer("⚠️ No es tuyo", show_alert=True)
            return
    else:
        action = data

    if action == "gen_cancel":
        await query.edit_message_text("❌ Cancelado.", reply_markup=build_main_menu())
        return

    if action != "gen_confirm":
        return

    # Verificar créditos
    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        await query.edit_message_text("❌ Créditos insuficientes.", reply_markup=build_main_menu())
        return

    # Verificar si ya tiene generación activa
    with _futures_lock:
        if user.id in _active_futures and not _active_futures[user.id].done():
            await query.edit_message_text("⏳ Ya tienes una generación en curso.", reply_markup=build_main_menu())
            return

    # Verificar capacidad
    if get_active_generations_count() >= MAX_WORKERS:
        await query.edit_message_text("⏳ Sistema lleno.", reply_markup=build_main_menu())
        return

    # Descontar créditos
    if not db.deduct_credits(user.id, CREDITS_PER_GEN):
        await query.edit_message_text("❌ Error al descontar.", reply_markup=build_main_menu())
        return

    credits_left = db.get_credits(user.id)

    await query.edit_message_text(
        f"⏳ <b>Generando cuenta...</b>\n\n"
        f"💰 Descontados: {CREDITS_PER_GEN}\n"
        f"💳 Balance: {credits_left}\n\n"
        f"✅ Te aviso cuando termine.",
        parse_mode=ParseMode.HTML,
    )

    app = ctx.application
    loop = asyncio.get_running_loop()
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
                
                asyncio.run_coroutine_threadsafe(
                    send_success_message(app, user.id, phone, password, name, cookies, elapsed),
                    loop
                )
            else:
                db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
                credits_restored = db.get_credits(user.id)
                asyncio.run_coroutine_threadsafe(
                    send_error_message(app, user.id, result["error"], credits_restored),
                    loop
                )
        except concurrent.futures.TimeoutError:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            asyncio.run_coroutine_threadsafe(
                send_error_message(app, user.id, "Timeout excedido", db.get_credits(user.id)),
                loop
            )
        except Exception as e:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            asyncio.run_coroutine_threadsafe(
                send_error_message(app, user.id, str(e), db.get_credits(user.id)),
                loop
            )
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
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"   📊 CONFIGURACIÓN:")
    print(f"   ├─ ADMIN_IDS:       {ADMIN_IDS}")
    print(f"   ├─ CREDITS_PER_GEN: {CREDITS_PER_GEN}")
    print(f"   ├─ MAX_WORKERS:     {MAX_WORKERS}")
    print(f"   ├─ PORT:            {PORT}")
    print(f"   └─ DB_FILE:         {DB_FILE}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    # Iniciar health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    print("✅ Health server iniciado")

    # Crear aplicación
    app = Application.builder().token(BOT_TOKEN).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("creditos", cmd_creditos))
    app.add_handler(CommandHandler("gen", cmd_gen))
    app.add_handler(CommandHandler("dar", cmd_dar))
    app.add_handler(CommandHandler("quitar", cmd_quitar))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("usuarios", cmd_usuarios))
    
    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_menu, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(callback_gen, pattern="^gen_"))

    print("✅ Bot iniciado correctamente. Esperando mensajes...")
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