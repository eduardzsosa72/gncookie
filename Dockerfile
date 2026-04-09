FROM ubuntu:22.04

WORKDIR /app

# Instalar dependencias del sistema
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    wget \
    gnupg \
    curl \
    libxml2-dev \
    libxslt1-dev \
    unzip \
    build-essential \
    zlib1g-dev \
    libncurses5-dev \
    libgdbm-dev \
    libnss3-dev \
    libreadline-dev \
    libsqlite3-dev \
    tk-dev \
    libbz2-dev \
    && rm -rf /var/lib/apt/lists/*

# Instalar Python 3.14 desde source
RUN wget https://www.python.org/ftp/python/3.14.0a3/Python-3.14.0a3.tgz \
    && tar -xzf Python-3.14.0a3.tgz \
    && cd Python-3.14.0a3 \
    && ./configure --enable-optimizations \
    && make -j $(nproc) \
    && make altinstall \
    && cd .. \
    && rm -rf Python-3.14.0a3*

# Crear symlink
RUN ln -s /usr/local/bin/python3.14 /usr/local/bin/python

# Instalar Google Chrome
RUN wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /etc/apt/trusted.gpg.d/google.gpg \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# Actualizar pip
RUN python -m ensurepip --upgrade

# Copiar requirements.txt
COPY requirements.txt .

# Instalar dependencias Python
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código
COPY . .

# Crear directorio para datos
RUN mkdir -p /app/data

EXPOSE 8080
CMD ["python", "bot.py"]