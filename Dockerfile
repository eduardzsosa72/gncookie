FROM python:3.14-rc-slim

WORKDIR /app

# Evita problemas de buffer
ENV PYTHONUNBUFFERED=1

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el proyecto
COPY . .

# Puerto para Railway
ENV PORT=8080

CMD ["python", "bot.py"]