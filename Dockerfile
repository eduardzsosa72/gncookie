FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y gcc g++ libffi-dev libssl-dev wget gnupg curl libxml2-dev libxslt1-dev && rm -rf /var/lib/apt/lists/*
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list && apt-get update && apt-get install -y google-chrome-stable && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/data
CMD ["python", "bot.py"]
