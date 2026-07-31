FROM python:3.11-slim

# Instalar ffmpeg una sola vez y limpiar caché para aligerar la imagen
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del código
COPY . .

CMD ["python", "server.py"]
