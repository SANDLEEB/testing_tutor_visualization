# Production image for the Testing Tutor NiceGUI app.
# Base image ships Python + NiceGUI preinstalled; we layer the app's own
# dependencies (SQLAlchemy, psycopg, authlib, etc.) on top.
FROM zauberzeug/nicegui:latest

WORKDIR /app

# Install dependencies first so this layer is cached across builds that only
# change application code.
#
# The base image's venv (/opt/venv) is built with `uv sync`, which does NOT
# install pip into it — `python -m pip install` fails with "No module named
# pip". Use `uv pip install` instead (same tool the base image's own
# Dockerfile uses to add packages); UV_PROJECT_ENVIRONMENT, inherited from the
# base image, points it at /opt/venv.
COPY codeflow/requirements.txt ./requirements.txt
RUN uv pip install --no-cache -r requirements.txt

# App source (codeflow/ is the actual application root in this repo).
COPY codeflow/ ./

EXPOSE 8080

CMD ["/opt/venv/bin/python", "main.py"]
