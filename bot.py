"""
Amazon Cookie Gen — Bot de Telegram
=====================================
Comandos:
  /start             — Bienvenida e info
  /gen               — Generar cuenta Amazon (cuesta 15 créditos)
  /creditos          — Ver tus créditos actuales

Solo Admin:
  /dar @usuario N    — Dar N créditos a un usuario
  /quitar @usuario N — Quitar N créditos
  /creditos @user    — Ver créditos de cualquier usuario
  /stats             — Stats globales del bot
  /usuarios          — Listar todos los usuarios
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

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.constants import ParseMode

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
CREDITS_PER_GEN = int(os.getenv("CREDITS_PER_GEN", "15"))
DB_FILE = os.getenv("DB_FILE", "bot_db.json")
ADMIN_DISPLAY_NAME = os.getenv("ADMIN_DISPLAY_NAME", "BILLY")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("amzbot")


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
_event_loop: asyncio.AbstractEventLoop = None  # Se asignará en main()


def run_create_account_isolated(user_id: int) -> dict:
    """
    Ejecuta create_account() de main.py de forma AISLADA.
    Cada llamada crea su propia instancia de AmazonCreator.
    """
    try:
        import main as main_module
        
        # Crear instancia DEDICADA para este usuario
        creator = main_module.AmazonCreator()
        
        # Ejecutar creación
        result = creator.create_account()
        
        # Validar resultado
        if isinstance(result, dict) and "phone" in result:
            return result
        else:
            return {"error": "Resultado inválido de create_account"}
            
    except Exception as e:
        logger.error(f"[User {user_id}] Error en generación: {e}", exc_info=True)
        return {"error": str(e)}


def get_active_generations_count() -> int:
    """Obtiene cuántas generaciones están activas actualmente"""
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


def fmt_user(user) -> str:
    name = user.first_name or ""
    if user.username:
        return f"{name} (@{user.username})"
    return f"{name} [ID:{user.id}]"


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


def split_text(text: str, max_len: int = 3500) -> list[str]:
    if not text:
        return []
    return [text[i:i + max_len] for i in range(0, len(text), max_len)]


def count_cookies(cookie_string: str) -> int:
    return len([c for c in cookie_string.split(";") if c.strip() and "=" in c])


# =============================================================================
# FUNCIONES DE ENVÍO (asíncronas, llamadas desde hilos con run_coroutine_threadsafe)
# =============================================================================

async def send_gen_success_async(app, user_id: int, phone: str, password: str, name: str, cookies: str, cookie_count: int, elapsed: float):
    """Versión asíncrona para enviar mensajes"""
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
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📋 <i>Las cookies están en el siguiente mensaje:</i>"
    )
    
    try:
        await app.bot.send_message(
            user_id,
            header_text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
        
        # Enviar cookies
        if safe_cookies:
            chunks = split_text(safe_cookies, 3500)
            for idx, chunk in enumerate(chunks, start=1):
                if len(chunks) == 1:
                    cookie_msg = f"🍪 <b>Cookies:</b>\n\n<code>{chunk}</code>"
                else:
                    cookie_msg = f"🍪 <b>Cookies ({idx}/{len(chunks)})</b>\n\n<code>{chunk}</code>"
                
                await app.bot.send_message(user_id, cookie_msg, parse_mode=ParseMode.HTML)
        else:
            await app.bot.send_message(user_id, "⚠️ No se recibieron cookies.", parse_mode=ParseMode.HTML)
            
    except Exception as e:
        logger.error(f"Error enviando mensaje de éxito a {user_id}: {e}")


async def send_gen_error_async(app, user_id: int, error_msg: str, credits_restored: int):
    """Versión asíncrona para enviar error"""
    try:
        await app.bot.send_message(
            user_id,
            f"❌ <b>Error al generar la cuenta</b>\n\n"
            f"<code>{html.escape(error_msg[:300])}</code>\n\n"
            f"💳 <b>Tus créditos han sido devueltos</b>\n"
            f"Balance actual: <b>{credits_restored}</b>\n\n"
            f"🔄 Intenta nuevamente con /gen\n"
            f"Si el error persiste, contacta al administrador.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
    except Exception as e:
        logger.error(f"Error enviando mensaje de error a {user_id}: {e}")


async def notify_admin_success_async(app, admin_id: int, user, phone: str, name: str, cookie_count: int, elapsed: float):
    """Notifica a admin sobre generación exitosa"""
    username_tag = f"@{user.username}" if user.username else f"ID:{user.id}"
    try:
        await app.bot.send_message(
            admin_id,
            f"🔔 <b>Nueva generación exitosa</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {html.escape(user.first_name or '')} ({html.escape(username_tag)})\n"
            f"📞 {html.escape(phone)}\n"
            f"👤 {html.escape(name)}\n"
            f"🍪 Cookies: {cookie_count}\n"
            f"⏱ Tiempo: {elapsed:.2f}s",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def notify_admin_error_async(app, admin_id: int, user, error_msg: str):
    """Notifica a admin sobre error en generación"""
    username_tag = f"@{user.username}" if user.username else f"ID:{user.id}"
    try:
        await app.bot.send_message(
            admin_id,
            f"⚠️ <b>Error en generación</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 {html.escape(user.first_name or '')} ({html.escape(username_tag)})\n"
            f"❌ Error: <code>{html.escape(error_msg[:200])}</code>",
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


# =============================================================================
# MANEJADORES DE COMANDOS
# =============================================================================

async def send_main_menu(chat_id: int, user, ctx: ContextTypes.DEFAULT_TYPE):
    db.upsert_user(user.id, user.username, user.first_name)
    credits = db.get_credits(user.id)
    active_gens = get_active_generations_count()

    if is_admin(user.id):
        display_name = html.escape(ADMIN_DISPLAY_NAME)
        welcome = f"👑 Bienvenido Administrador <b>{display_name}</b>"
    else:
        display_name = html.escape(user.first_name or "Usuario")
        welcome = f"👤 Bienvenido <b>{display_name}</b>"

    text = (
        f"⚡ <b>Cookie Gen Amazon</b> ⚡\n"
        f"━━━━━━━━━━━━━━━━━\n\n"
        f"{welcome}\n"
        f"💰 Créditos disponibles: <b>{credits}</b>\n"
        f"🔄 Generaciones activas: <b>{active_gens}/{MAX_WORKERS}</b>\n\n"
        f"📋 Selecciona una opción:"
    )

    await ctx.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await send_main_menu(update.effective_chat.id, user, ctx)


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await send_main_menu(update.effective_chat.id, user, ctx)


async def cmd_creditos(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)

    if ctx.args and is_admin(user.id):
        target_arg = ctx.args[0].lstrip("@")
        target = db.find_user_by_username(target_arg)

        if not target_arg.isdigit() and not target:
            await update.message.reply_text(
                f"❌ Usuario <code>{html.escape(ctx.args[0])}</code> no encontrado.",
                parse_mode=ParseMode.HTML,
            )
            return

        if target_arg.isdigit():
            target = db.get_user(int(target_arg))

        if not target:
            await update.message.reply_text("❌ Usuario no encontrado.")
            return

        credits = target["credits"]
        gen = target["total_generated"]
        username = f"@{target['username']}" if target.get("username") else f"ID:{target['id']}"
        text = (
            f"👤 <b>{html.escape(target['first_name'])}</b> ({html.escape(username)})\n"
            f"💳 Créditos: <b>{credits}</b>\n"
            f"🔄 Generaciones: <b>{gen}</b>\n"
            f"📅 Ingresó: {target.get('joined_at','?')[:10]}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return

    u = db.get_user(user.id)
    credits = u["credits"] if u else 0
    gen = u["total_generated"] if u else 0

    text = (
        f"💰 <b>Tus créditos</b>\n\n"
        f"Balance: <b>{credits}</b> créditos\n"
        f"{credits_bar(credits)}\n\n"
        f"🔄 Generaciones realizadas: <b>{gen}</b>\n"
        f"💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>\n"
        f"🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces más"
    )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=build_main_menu(),
    )


async def cmd_dar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores pueden dar créditos.")
        return

    if len(ctx.args) < 2:
        await update.message.reply_text("📝 Uso: /dar @usuario <cantidad>")
        return

    target_arg = ctx.args[0].lstrip("@")
    amount_str = ctx.args[1]

    if not amount_str.isdigit() or int(amount_str) <= 0:
        await update.message.reply_text("❌ La cantidad debe ser un número positivo.")
        return

    amount = int(amount_str)

    target = (
        db.find_user_by_username(target_arg)
        if not target_arg.isdigit()
        else db.get_user(int(target_arg))
    )

    if not target:
        await update.message.reply_text(
            f"❌ Usuario <code>{html.escape(ctx.args[0])}</code> no encontrado.\n"
            f"⚠️ El usuario debe haber iniciado el bot con /start primero.",
            parse_mode=ParseMode.HTML,
        )
        return

    new_balance = db.add_credits(target["id"], amount, user.id, "dar")
    username = f"@{target['username']}" if target.get("username") else f"ID:{target['id']}"

    text = (
        f"✅ <b>Créditos otorgados</b>\n\n"
        f"👤 Usuario: <b>{html.escape(target['first_name'])}</b> ({html.escape(username)})\n"
        f"➕ Agregados: <b>{amount}</b> créditos\n"
        f"💳 Balance nuevo: <b>{new_balance}</b> créditos"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

    try:
        await ctx.bot.send_message(
            chat_id=target["id"],
            text=(
                f"💰 <b>¡Recibiste créditos!</b>\n\n"
                f"➕ <b>+{amount}</b> créditos\n"
                f"💳 Tu balance: <b>{new_balance}</b>\n\n"
                f"Usa /gen para generar una cuenta Amazon"
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass


async def cmd_quitar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    if not is_admin(user.id):
        await update.message.reply_text("⛔ Solo administradores pueden quitar créditos.")
        return

    if len(ctx.args) < 2:
        await update.message.reply_text("📝 Uso: /quitar @usuario <cantidad>")
        return

    target_arg = ctx.args[0].lstrip("@")
    amount_str = ctx.args[1]

    if not amount_str.isdigit() or int(amount_str) <= 0:
        await update.message.reply_text("❌ La cantidad debe ser un número positivo.")
        return

    amount = int(amount_str)
    target = (
        db.find_user_by_username(target_arg)
        if not target_arg.isdigit()
        else db.get_user(int(target_arg))
    )

    if not target:
        await update.message.reply_text(
            f"❌ Usuario <code>{html.escape(ctx.args[0])}</code> no encontrado.",
            parse_mode=ParseMode.HTML,
        )
        return

    new_balance = db.add_credits(target["id"], -amount, user.id, "quitar")
    username = f"@{target['username']}" if target.get("username") else f"ID:{target['id']}"

    text = (
        f"✅ <b>Créditos removidos</b>\n\n"
        f"👤 Usuario: <b>{html.escape(target['first_name'])}</b> ({html.escape(username)})\n"
        f"➖ Removidos: <b>{amount}</b> créditos\n"
        f"💳 Balance nuevo: <b>{new_balance}</b> créditos"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
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
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_usuarios(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Solo administradores.")
        return

    users = db.get_all_users()
    if not users:
        await update.message.reply_text("📭 No hay usuarios registrados.")
        return

    users.sort(key=lambda u: u["credits"], reverse=True)

    lines = ["👥 <b>Usuarios registrados</b>\n"]
    for u in users[:30]:
        username = f"@{u['username']}" if u.get("username") else f"ID:{u['id']}"
        lines.append(
            f"• <b>{html.escape(u['first_name'])}</b> ({html.escape(username)})\n"
            f"  💳 {u['credits']} créditos | 🔄 {u['total_generated']} gens"
        )

    if len(users) > 30:
        lines.append(f"\n... y {len(users) - 30} más")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# =============================================================================
# GENERACIÓN DE CUENTA (PARALELA)
# =============================================================================

async def cmd_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Comando /gen - Inicia generación de cuenta"""
    user = update.effective_user
    db.upsert_user(user.id, user.username, user.first_name)
    
    # Verificar si ya tiene una generación activa
    with _futures_lock:
        existing_future = _active_futures.get(user.id)
        if existing_future and not existing_future.done():
            await update.message.reply_text(
                "⏳ <b>Ya tienes una generación en curso</b>\n\n"
                "Por favor espera a que termine antes de iniciar otra.\n"
                "El proceso puede tomar 2-4 minutos.",
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(),
            )
            return
    
    # Verificar créditos
    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        needed = CREDITS_PER_GEN - credits
        await update.message.reply_text(
            f"❌ <b>Créditos insuficientes</b>\n\n"
            f"💳 Tienes: <b>{credits}</b>\n"
            f"💰 Necesitas: <b>{CREDITS_PER_GEN}</b>\n"
            f"📉 Faltan: <b>{needed}</b>\n\n"
            f"Contacta con el administrador para obtener más créditos.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
        return
    
    # Verificar si hay espacio en el pool
    active_gens = get_active_generations_count()
    if active_gens >= MAX_WORKERS:
        await update.message.reply_text(
            f"⏳ <b>Sistema ocupado</b>\n\n"
            f"Actualmente hay <b>{active_gens}/{MAX_WORKERS}</b> generaciones en curso.\n"
            f"Por favor espera unos minutos e intenta nuevamente.\n\n"
            f"💳 Tus créditos: <b>{credits}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
        return
    
    # Mostrar confirmación
    await update.message.reply_text(
        f"🛒 <b>Generar cuenta Amazon</b>\n\n"
        f"💳 Tus créditos: <b>{credits}</b>\n"
        f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
        f"💳 Quedarás con: <b>{credits - CREDITS_PER_GEN}</b>\n\n"
        f"⚠️ <b>Importante:</b>\n"
        f"• El proceso tarda <b>2-4 minutos</b>\n"
        f"• Capacidad actual: <b>{active_gens + 1}/{MAX_WORKERS}</b>\n"
        f"• Recibirás las cookies cuando termine\n\n"
        f"¿Confirmas la generación?",
        parse_mode=ParseMode.HTML,
        reply_markup=build_confirm_menu(user.id),
    )


async def callback_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback para menú principal"""
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    db.upsert_user(user.id, user.username, user.first_name)

    if data == "menu_creditos":
        u = db.get_user(user.id)
        credits = u["credits"] if u else 0
        gen = u["total_generated"] if u else 0

        text = (
            f"💰 <b>Tus créditos</b>\n\n"
            f"Balance: <b>{credits}</b> créditos\n"
            f"{credits_bar(credits)}\n\n"
            f"🔄 Generaciones realizadas: <b>{gen}</b>\n"
            f"💰 Costo por generación: <b>{CREDITS_PER_GEN}</b>\n"
            f"🔮 Puedes generar: <b>{credits // CREDITS_PER_GEN}</b> veces más"
        )
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=build_main_menu(),
        )
        return

    if data == "menu_amazon":
        credits = db.get_credits(user.id)
        active_gens = get_active_generations_count()

        # Verificar si ya tiene generación activa
        with _futures_lock:
            existing_future = _active_futures.get(user.id)
            if existing_future and not existing_future.done():
                await query.edit_message_text(
                    "⏳ <b>Ya tienes una generación en curso</b>\n\n"
                    "Por favor espera a que termine.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=build_main_menu(),
                )
                return

        if credits < CREDITS_PER_GEN:
            needed = CREDITS_PER_GEN - credits
            await query.edit_message_text(
                text=(
                    f"❌ <b>Créditos insuficientes</b>\n\n"
                    f"💳 Tienes: <b>{credits}</b> créditos\n"
                    f"💰 Necesitas: <b>{CREDITS_PER_GEN}</b> créditos\n"
                    f"📉 Faltan: <b>{needed}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(),
            )
            return

        if active_gens >= MAX_WORKERS:
            await query.edit_message_text(
                text=(
                    f"⏳ <b>Sistema ocupado</b>\n\n"
                    f"Actualmente hay <b>{active_gens}/{MAX_WORKERS}</b> generaciones en curso.\n"
                    f"Por favor espera unos minutos e intenta nuevamente.\n\n"
                    f"💳 Tus créditos: <b>{credits}</b>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=build_main_menu(),
            )
            return

        await query.edit_message_text(
            text=(
                f"🛒 <b>Generar cuenta Amazon</b>\n\n"
                f"💳 Tus créditos: <b>{credits}</b>\n"
                f"💰 Costo: <b>{CREDITS_PER_GEN}</b>\n"
                f"💳 Quedarás con: <b>{credits - CREDITS_PER_GEN}</b>\n\n"
                f"⚠️ El proceso tarda <b>2-4 minutos</b>\n"
                f"Capacidad: <b>{active_gens + 1}/{MAX_WORKERS}</b>\n\n"
                f"¿Confirmas?"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=build_confirm_menu(user.id),
        )
        return


async def callback_gen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Callback para confirmar/cancelar generación"""
    query = update.callback_query
    user = query.from_user
    data = query.data
    await query.answer()

    # Validar que el botón pertenece al usuario
    if ":" in data:
        action, uid_str = data.rsplit(":", 1)
        if uid_str.isdigit() and int(uid_str) != user.id:
            await query.answer("⚠️ Este botón no es tuyo.", show_alert=True)
            return
    else:
        action = data

    if action == "gen_cancel":
        await query.edit_message_text(
            "❌ Generación cancelada.\n\n"
            "Puedes intentar nuevamente cuando quieras.",
            reply_markup=build_main_menu(),
        )
        return

    if action != "gen_confirm":
        return

    # Verificar créditos nuevamente (podrían haber cambiado)
    credits = db.get_credits(user.id)
    if credits < CREDITS_PER_GEN:
        await query.edit_message_text(
            f"❌ Ya no tienes suficientes créditos ({credits}).",
            reply_markup=build_main_menu(),
        )
        return

    # Verificar si ya tiene una generación activa
    with _futures_lock:
        if user.id in _active_futures and not _active_futures[user.id].done():
            await query.edit_message_text(
                "⏳ Ya tienes una generación en curso.",
                reply_markup=build_main_menu(),
            )
            return

    # Verificar capacidad del pool
    active_gens = get_active_generations_count()
    if active_gens >= MAX_WORKERS:
        await query.edit_message_text(
            f"⏳ Sistema lleno ({active_gens}/{MAX_WORKERS}). Intenta más tarde.",
            reply_markup=build_main_menu(),
        )
        return

    # Descontar créditos
    if not db.deduct_credits(user.id, CREDITS_PER_GEN):
        await query.edit_message_text(
            "❌ Error al descontar créditos.",
            reply_markup=build_main_menu(),
        )
        return

    credits_left = db.get_credits(user.id)

    # Mensaje de inicio
    await query.edit_message_text(
        f"⏳ <b>Generando cuenta Amazon...</b>\n\n"
        f"💰 Créditos descontados: <b>{CREDITS_PER_GEN}</b>\n"
        f"💳 Balance restante: <b>{credits_left}</b>\n\n"
        f"🔄 Generaciones activas: <b>{active_gens + 1}/{MAX_WORKERS}</b>\n\n"
        f"✅ <b>El proceso ha comenzado</b>\n"
        f"📱 Te notificaré cuando termine (2-4 minutos)\n"
        f"🔔 <i>No cierres esta conversación</i>",
        parse_mode=ParseMode.HTML,
    )

    # Guardar referencias
    app = ctx.application
    loop = _event_loop

    # Ejecutar generación en el ThreadPoolExecutor
    future = _executor.submit(run_create_account_isolated, user.id)
    
    with _futures_lock:
        _active_futures[user.id] = future

    # Función para procesar el resultado en segundo plano
    def check_result():
        try:
            result = future.result(timeout=300)  # 5 minutos timeout
            
            if "error" not in result:
                phone = result.get("phone", "")
                password = result.get("password", "")
                name = result.get("name", "")
                cookies = result.get("cookies", "")
                elapsed = result.get("elapsed", 0)
                
                cookie_count = count_cookies(cookies)
                
                # Guardar en DB
                db.record_gen(user.id, user.username or "", phone, name)
                
                # Enviar resultado al usuario (usando asyncio.run_coroutine_threadsafe)
                asyncio.run_coroutine_threadsafe(
                    send_gen_success_async(app, user.id, phone, password, name, cookies, cookie_count, elapsed),
                    loop
                )
                
                # Notificar a admins
                for admin_id in ADMIN_IDS:
                    asyncio.run_coroutine_threadsafe(
                        notify_admin_success_async(app, admin_id, user, phone, name, cookie_count, elapsed),
                        loop
                    )
                    
            else:
                # Devolver créditos en caso de error
                db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
                credits_restored = db.get_credits(user.id)
                
                asyncio.run_coroutine_threadsafe(
                    send_gen_error_async(app, user.id, result["error"], credits_restored),
                    loop
                )
                
                for admin_id in ADMIN_IDS:
                    asyncio.run_coroutine_threadsafe(
                        notify_admin_error_async(app, admin_id, user, result["error"]),
                        loop
                    )
                    
        except concurrent.futures.TimeoutError:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            credits_restored = db.get_credits(user.id)
            asyncio.run_coroutine_threadsafe(
                send_gen_error_async(app, user.id, "Timeout de 5 minutos excedido", credits_restored),
                loop
            )
            
        except Exception as e:
            db.add_credits(user.id, CREDITS_PER_GEN, 0, "refund")
            credits_restored = db.get_credits(user.id)
            asyncio.run_coroutine_threadsafe(
                send_gen_error_async(app, user.id, str(e), credits_restored),
                loop
            )
            
        finally:
            with _futures_lock:
                _active_futures.pop(user.id, None)

    # Ejecutar callback en hilo separado
    threading.Thread(target=check_result, daemon=True).start()


# =============================================================================
# MAIN
# =============================================================================

def main():
    global _event_loop
    
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN no configurado.")
        print("   Agrega BOT_TOKEN=tu_token en el .env o como variable de entorno.")
        sys.exit(1)

    if not ADMIN_IDS:
        print("⚠️ ADMIN_IDS no configurado.")
        print("   Agrega ADMIN_IDS=123456,789012 en el .env.")

    print("🤖 Iniciando bot de Amazon Cookie Gen...")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"   📊 CONFIGURACIÓN:")
    print(f"   ├─ ADMIN_IDS:       {ADMIN_IDS}")
    print(f"   ├─ CREDITS_PER_GEN: {CREDITS_PER_GEN}")
    print(f"   ├─ MAX_WORKERS:     {MAX_WORKERS}")
    print(f"   └─ DB_FILE:         {DB_FILE}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("✅ Bot corriendo. Presiona Ctrl+C para detener.")
    print()

    # Crear y guardar el event loop para usarlo desde los hilos
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

    # Callbacks
    app.add_handler(CallbackQueryHandler(callback_menu, pattern="^menu_"))
    app.add_handler(CallbackQueryHandler(callback_gen, pattern="^gen_"))

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    # Recargar variables después de dotenv
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    CREDITS_PER_GEN = int(os.getenv("CREDITS_PER_GEN", "15"))
    DB_FILE = os.getenv("DB_FILE", "bot_db.json")
    ADMIN_DISPLAY_NAME = os.getenv("ADMIN_DISPLAY_NAME", "BILLY")
    MAX_WORKERS = int(os.getenv("MAX_WORKERS", "3"))
    
    main()