import asyncio
import json
import os
import logging
import tempfile
import inspect
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Importar prime.py
from prime import create

# ================= CONFIG =================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en Railway Variables")

ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "6319087504"))
COST_GENERATE = int(os.getenv("COST_GENERATE", "10"))

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)

CREDITS_FILE = Path(DATA_DIR) / "credits.json"
LOCK = asyncio.Lock()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
)
logger = logging.getLogger("REZE-BOT")


# ================= CREDITOS =================
def load_credits() -> dict:
    try:
        if not CREDITS_FILE.exists():
            return {}
        with open(CREDITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("No se pudo leer credits.json")
        return {}


async def save_credits(credits: dict):
    async with LOCK:
        with open(CREDITS_FILE, "w", encoding="utf-8") as f:
            json.dump(credits, f, indent=2)


async def get_credits(user_id: int) -> int:
    credits = load_credits()
    return int(credits.get(str(user_id), 0))


async def add_credits(user_id: int, amount: int) -> int:
    credits = load_credits()
    uid = str(user_id)
    credits[uid] = int(credits.get(uid, 0)) + amount
    await save_credits(credits)
    return credits[uid]


async def deduct_credits(user_id: int, amount: int) -> bool:
    credits = load_credits()
    uid = str(user_id)
    current = int(credits.get(uid, 0))

    if current < amount:
        return False

    credits[uid] = current - amount
    await save_credits(credits)
    return True


# ================= PRIME WRAPPER =================
async def run_prime_create():
    """
    Soporta prime.create() async o normal.
    """
    result = create()

    if inspect.isawaitable(result):
        result = await result

    return result


# ================= COMANDOS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛒 *REZE COOKIES   *\n\n"
        "Usa /credits para ver tu saldo.\n"
        f"Usa /generate para generar. Cuesta {COST_GENERATE} créditos.\n\n"
        "Admin: /crd <user_id> <cantidad>",
        parse_mode="Markdown"
    )


async def credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bal = await get_credits(user_id)

    await update.message.reply_text(
        f"💰 Tus créditos: *{bal}*",
        parse_mode="Markdown"
    )


async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await deduct_credits(user_id, COST_GENERATE):
        await update.message.reply_text(
            f"❌ No tienes suficientes créditos. Necesitas {COST_GENERATE}."
        )
        return

    await update.message.reply_text("⏳ Generando...")

    try:
        result = await run_prime_create()

        if not isinstance(result, dict):
            await add_credits(user_id, COST_GENERATE)
            await update.message.reply_text(
                "❌ prime.py no devolvió un diccionario válido.\n"
                f"Respuesta: {result}\n\n"
                f"Se reembolsaron {COST_GENERATE} créditos."
            )
            return

        if result.get("status") and result.get("cookies"):
            email = result.get("email", "No disponible")
            password = result.get("password", "No disponible")
            phone = result.get("phone", "No disponible")
            cookies = result.get("cookies", "")

            msg = (
                "✅ *Generación completada*\n\n"
                f"📧 *Email:* `{email}`\n"
                f"🔑 *Password:* `{password}`\n"
                f"📱 *Teléfono:* `{phone}`"
            )

            await update.message.reply_text(msg, parse_mode="Markdown")

            if len(cookies) <= 3500:
                await update.message.reply_text(
                    f"🍪 *Cookies:*\n`{cookies}`",
                    parse_mode="Markdown"
                )
            else:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".txt",
                    delete=False,
                    encoding="utf-8"
                ) as f:
                    f.write(cookies)
                    temp_path = f.name

                with open(temp_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename="cookies.txt",
                        caption="🍪 Cookies completas."
                    )

                os.unlink(temp_path)

        else:
            error_msg = result.get("error", "Error desconocido")
            await add_credits(user_id, COST_GENERATE)

            await update.message.reply_text(
                f"❌ Falló la generación:\n`{error_msg}`\n\n"
                f"Se reembolsaron {COST_GENERATE} créditos.",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.exception("Error ejecutando prime.create()")
        await add_credits(user_id, COST_GENERATE)

        await update.message.reply_text(
            f"⚠️ Error interno:\n`{str(e)}`\n\n"
            f"Se reembolsaron {COST_GENERATE} créditos.",
            parse_mode="Markdown"
        )


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

        await update.message.reply_text(
            f"✅ Créditos agregados.\n\n"
            f"👤 Usuario: `{target_id}`\n"
            f"➕ Cantidad: `{amount}`\n"
            f"💰 Nuevo saldo: `{new_bal}`",
            parse_mode="Markdown"
        )

    except ValueError:
        await update.message.reply_text("❌ user_id y cantidad deben ser números.")


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 Tu ID es:\n`{update.effective_user.id}`",
        parse_mode="Markdown"
    )


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