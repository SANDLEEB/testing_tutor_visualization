# Production image for the Testing Tutor NiceGUI app.
# Base image ships Python + NiceGUI preinstalled; we layer the app's own
# dependencies (SQLAlchemy, psycopg, authlib, etc.) on top.
FROM zauberzeug/nicegui:latest

WORKDIR /app

# Install dependencies first so this layer is cached across builds that only
# change application code.
COPY codeflow/requirements.txt ./requirements.txt
RUN python3 -m pip install --no-cache-dir -r requirements.txt

# App source (codeflow/ is the actual application root in this repo).
COPY codeflow/ ./

EXPOSE 8080

CMD ["python3", "main.py"]
