import asyncio
import json
import os
import logging
import tempfile
import inspect
from pathlib import Path

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
    except Exception:
        logger.exception("Error leyendo credits.json")
        return {}


async def save_credits(data):
    async with LOCK:
        with open(CREDITS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


async def get_credits(user_id):
    return int(load_credits().get(str(user_id), 0))


async def add_credits(user_id, amount):
    data = load_credits()
    uid = str(user_id)

    data[uid] = int(data.get(uid, 0)) + amount
    await save_credits(data)

    return data[uid]


async def deduct_credits(user_id, amount):
    data = load_credits()
    uid = str(user_id)
    current = int(data.get(uid, 0))

    if current < amount:
        return False

    data[uid] = current - amount
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
        "Comandos:\n"
        "/credits - Ver tus créditos\n"
        f"/generate - Generar cuenta, cuesta {COST_GENERATE} créditos\n"
        "/me - Ver tu ID\n\n"
    )


async def credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bal = await get_credits(update.effective_user.id)
    await update.message.reply_text(f"Tus créditos: {bal}")


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await deduct_credits(user_id, COST_GENERATE):
        bal = await get_credits(user_id)
        await update.message.reply_text(
            f"No tienes créditos suficientes.\n"
            f"Necesitas: {COST_GENERATE}\n"
            f"Tus créditos: {bal}"
        )
        return

    await update.message.reply_text("Generando cuenta...")

    try:
        result = await run_create()

        if not isinstance(result, dict):
            await add_credits(user_id, COST_GENERATE)
            remaining = await get_credits(user_id)

            await update.message.reply_text(
                "Error: api  no devolvió una respuesta válida.\n"
                f"Respuesta: {result}\n"
                "Reembolso aplicado.\n"
                f"Créditos actuales: {remaining}"
            )
            return

        if not result.get("status"):
            await add_credits(user_id, COST_GENERATE)
            remaining = await get_credits(user_id)

            await update.message.reply_text(
                f"Error: {result.get('error', 'desconocido')}\n"
                "Reembolso aplicado.\n"
                f"Créditos actuales: {remaining}"
            )
            return

        email = result.get("email", "No disponible")
        password = result.get("password", "No disponible")
        phone = result.get("phone", "No disponible")
        cookies = result.get("cookies", "")
        creation_time = result.get("creation_time", "N/A")
        remaining = await get_credits(user_id)

        msg = (
            "Cuenta creada correctamente\n\n"
            f"Email: {email}\n"
            f"Pass: {password}\n"
            f"Phone: {phone}\n"
            f"Tiempo: {creation_time} segundos\n"
            f"Créditos restantes: {remaining}"
        )

        await update.message.reply_text(msg)

        if cookies:
            if len(cookies) < 3500:
                await update.message.reply_text(cookies)
            else:
                with tempfile.NamedTemporaryFile(
                    delete=False,
                    suffix=".txt",
                    mode="w",
                    encoding="utf-8"
                ) as f:
                    f.write(cookies)
                    path = f.name

                with open(path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename="cookies.txt",
                        caption="Cookies completas"
                    )

                os.unlink(path)
        else:
            await update.message.reply_text("No se recibieron cookies desde prime.py")

    except Exception as e:
        logger.exception("Error en /generate")
        await add_credits(user_id, COST_GENERATE)
        remaining = await get_credits(user_id)

        await update.message.reply_text(
            f"Error interno: {str(e)}\n"
            "Reembolso aplicado.\n"
            f"Créditos actuales: {remaining}"
        )


async def crd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if user_id != ADMIN_USER_ID:
        await update.message.reply_text("No autorizado")
        return

    if len(context.args) != 2:
        await update.message.reply_text("Uso: /crd user_id cantidad")
        return

    try:
        uid = int(context.args[0])
        amount = int(context.args[1])

        new_bal = await add_credits(uid, amount)

        await update.message.reply_text(
            f"Créditos añadidos correctamente\n"
            f"Usuario: {uid}\n"
            f"Cantidad: {amount}\n"
            f"Nuevo saldo: {new_bal}"
        )

    except ValueError:
        await update.message.reply_text("Error: user_id y cantidad deben ser números")


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Tu ID: {update.effective_user.id}")


# ================= MAIN =================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("credits", credits))
    app.add_handler(CommandHandler("generate", generate))
    app.add_handler(CommandHandler("crd", crd))
    app.add_handler(CommandHandler("me", me))

    logger.info("Bot iniciado correctamente")
    app.run_polling()


if __name__ == "__main__":
    main()