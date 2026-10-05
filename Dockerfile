FROM python:3.8-slim

# new: no .pyc files, unbuffered logs (so docker logs shows output immediately)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements_docker.txt .
RUN pip install --no-cache-dir -r requirements_docker.txt

# new: create non-root user, copy code owned by it
RUN useradd --create-home appuser
COPY --chown=appuser:appuser . .
USER appuser

EXPOSE 8000
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]