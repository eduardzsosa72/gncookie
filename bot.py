import asyncio
import json
import os
import logging
import tempfile
import inspect
from pathlib import Path
from unittest import result

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from prime import create

# ================= CONFIG =================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN")

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "6319087504"))
COST_GENERATE = int(os.getenv("COST_GENERATE", "10"))

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

CREDITS_FILE = Path(DATA_DIR) / "credits.json"
LOCK = asyncio.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("REZE-BOT")

# ================= CREDITOS =================
def load_credits():
    if not CREDITS_FILE.exists():
        return {}
    try:
        with open(CREDITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

async def save_credits(data):
    async with LOCK:
        with open(CREDITS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

async def get_credits(user_id):
    return load_credits().get(str(user_id), 0)

async def add_credits(user_id, amount):
    data = load_credits()
    uid = str(user_id)
    data[uid] = data.get(uid, 0) + amount
    await save_credits(data)
    return data[uid]

async def deduct_credits(user_id, amount):
    data = load_credits()
    uid = str(user_id)

    if data.get(uid, 0) < amount:
        return False

    data[uid] -= amount
    await save_credits(data)
    return True

# ================= PRIME =================
async def run_create():
    result = create()
    if inspect.isawaitable(result):
        result = await result
    return result

# ================= COMANDOS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "REZE CHK\n\n"
        "Usa /credits para ver tu saldo\n"
        f"Usa /generate (cuesta {COST_GENERATE})\n\n"
        "Admin: /crd user_id cantidad"
    )

async def credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = await get_credits(update.effective_user.id)
    await update.message.reply_text(f"Creditos: {bal}")

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await deduct_credits(user_id, COST_GENERATE):
        await update.message.reply_text("No tienes creditos")
        return

    await update.message.reply_text("Generando...")

    try:
        result = await run_create()

        if not result or not result.get("status"):
            await add_credits(user_id, COST_GENERATE)
            await update.message.reply_text(
                f"Error: {result.get('error', 'desconocido')}\nReembolso aplicado"
            )
            return

        email = result.get("email")
        password = result.get("password")
        phone = result.get("phone")
        cookies = result.get("cookies", "")
        creation_time = result.get("creation_time", "N/A")

        msg = (
            f"Cuenta creada\n\n"
            f"Email: {email}\n"
            f"Pass: {password}\n"
            f"Phone: {phone}\n"
            f"Creation Time: {creation_time}segundos "
        )

        await update.message.reply_text(msg)

        if len(cookies) < 3500:
            await update.message.reply_text(cookies)
        else:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".txt", mode="w", encoding="utf-8") as f:
                f.write(cookies)
                path = f.name

            with open(path, "rb") as f:
                await update.message.reply_document(f, filename="cookies.txt")

            os.unlink(path)

    except Exception as e:
        logger.exception("Error")
        await add_credits(user_id, COST_GENERATE)
        await update.message.reply_text(f"Error interno: {str(e)}")

async def crd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_USER_ID:
        await update.message.reply_text("No autorizado")
        return

    try:
        uid = int(context.args[0])
        amount = int(context.args[1])

        new_bal = await add_credits(uid, amount)

        await update.message.reply_text(
            f"Creditos añadidos\nUser: {uid}\nNuevo saldo: {new_bal}"
        )
    except:
        await update.message.reply_text("Uso: /crd user_id cantidad")

async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(str(update.effective_user.id))

# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("credits", credits))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("crd", crd))
    app.add_handler(CommandHandler("me", me))

    logger.info("Bot iniciado")
    app.run_polling()

if __name__ == "__main__":
    main()