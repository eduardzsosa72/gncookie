# Dockerfile
FROM python:3.10-slim

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todos los archivos
COPY . .

# Crear directorio para resultados
RUN mkdir -p results

# Exponer puerto
EXPOSE 8080

# Comando para ejecutar la API
CMD ["python", "api_enhanced.py"]