import asyncio
import json
import os
import logging
import tempfile
import signal
from pathlib import Path
from telegram import Update, Document
from telegram.ext import Application, CommandHandler, ContextTypes
import sys

# Asegurar que podemos importar prime
sys.path.append(os.path.dirname(__file__))
from prime import create

# ========== CONFIGURACIÓN ==========
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No se encontró TELEGRAM_BOT_TOKEN en variables de entorno")

ADMIN_USER_ID = 6319087504  # Tu ID de administrador

# Persistencia de créditos (Railway: usa /app/data si montas volumen)
DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
CREDITS_FILE = Path(DATA_DIR) / "credits.json"
LOCK = asyncio.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ========== FUNCIONES DE CRÉDITOS ==========
def load_credits() -> dict:
    if not CREDITS_FILE.exists():
        return {}
    with open(CREDITS_FILE, "r") as f:
        return json.load(f)

async def save_credits(credits: dict):
    async with LOCK:
        with open(CREDITS_FILE, "w") as f:
            json.dump(credits, f, indent=2)

async def get_credits(user_id: int) -> int:
    credits = load_credits()
    return credits.get(str(user_id), 0)

async def add_credits(user_id: int, amount: int) -> int:
    credits = load_credits()
    uid = str(user_id)
    new_bal = credits.get(uid, 0) + amount
    credits[uid] = new_bal
    await save_credits(credits)
    return new_bal

async def deduct_credits(user_id: int, amount: int) -> bool:
    credits = load_credits()
    uid = str(user_id)
    current = credits.get(uid, 0)
    if current < amount:
        return False
    credits[uid] = current - amount
    await save_credits(credits)
    return True

# ========== COMANDOS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *Amazon Account Generator*\n\n"
        "Usa /credits para ver tu saldo.\n"
        "Usa /generate para crear una cuenta (cuesta 10 créditos).\n"
        "Contacta al administrador para recargar créditos.\n\n",
        parse_mode="Markdown"
    )

async def credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = await get_credits(user_id)
    await update.message.reply_text(f"💰 Tus créditos: *{bal}*", parse_mode="Markdown")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not await deduct_credits(user_id, 10):
        await update.message.reply_text("❌ No tienes suficientes créditos (necesitas 10). Usa /credits.")
        return

    await update.message.reply_text("⏳ Generando cuenta de Amazon...")

    try:
        result = await create()
        if result and result.get("status") and result.get("cookies"):
            email = result.get("email", "No disponible")
            password = result.get("password", "No disponible")
            phone = result.get("phone", "No disponible")
            cookies = result.get("cookies", "")

            msg = (
                f"✅ *Cuenta creada exitosamente*\n\n"
                f"📧 *Email:* `{email}`\n"
                f"🔑 *Password:* `{password}`\n"
                f"📱 *Teléfono:* `{phone}`\n"
            )
            await update.message.reply_text(msg, parse_mode="Markdown")

            MAX_MESSAGE_LEN = 4096
            if len(cookies) <= MAX_MESSAGE_LEN:
                await update.message.reply_text(f"🍪 *Cookies:*\n`{cookies}`", parse_mode="Markdown")
            else:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
                    f.write(cookies)
                    temp_path = f.name
                with open(temp_path, "rb") as f:
                    await update.message.reply_document(
                        document=Document(f, filename="amazon_cookies.txt"),
                        caption="🍪 Cookies completas (el mensaje era demasiado largo, se adjunta archivo)."
                    )
                os.unlink(temp_path)
        else:
            error_msg = result.get("error", "Error desconocido") if result else "Sin resultado"
            await add_credits(user_id, 10)
            await update.message.reply_text(f"❌ Falló la generación: {error_msg}\nSe reembolsaron tus 10 créditos.")
    except Exception as e:
        logger.exception("Error en generate")
        await add_credits(user_id, 10)
        await update.message.reply_text(f"⚠️ Error interno: {str(e)}\nCréditos reembolsados.")

async def crd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("⛔ Solo el administrador puede usar este comando.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Uso: /crd <user_id> <cantidad>")
        return
    try:
        target_id = int(context.args[0])
        amount = int(context.args[1])
        new_bal = await add_credits(target_id, amount)
        await update.message.reply_text(f"✅ Se añadieron {amount} créditos al usuario {target_id}. Nuevo saldo: {new_bal}")
    except ValueError:
        await update.message.reply_text("Error: user_id y cantidad deben ser números.")

# ========== CIERRE LIMPIO (evita tareas pendientes) ==========
async def shutdown(application: Application):
    """Cierra correctamente el bot y cancela tareas pendientes."""
    logger.info("Iniciando apagado...")
    await application.stop()
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("Apagado completado.")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("credits", credits))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("crd", crd))

    # Manejar señales de terminación (SIGTERM para Railway)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    def signal_handler():
        asyncio.create_task(shutdown(app))
        loop.stop()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)
    
    logger.info("Bot iniciado. Esperando comandos...")
    try:
        loop.run_until_complete(app.initialize())
        loop.run_until_complete(app.start())
        loop.run_until_complete(app.updater.start_polling())
        loop.run_forever()
    except KeyboardInterrupt:
        logger.info("Interrupción manual.")
    finally:
        loop.run_until_complete(shutdown(app))
        loop.close()

if __name__ == "__main__":
    main()