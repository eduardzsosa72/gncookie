FROM python:3.11-slim

# Instalar curl y libcurl (necesarios para curl_cffi)
RUN apt-get update && apt-get install -y curl libcurl4-openssl-dev gcc && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar requirements e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

# Crear directorio para persistencia de créditos (opcional)
RUN mkdir -p /app/data

# Comando para ejecutar el bot
CMD ["python", "bot.py"]