FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY production/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY production/ ./production/
COPY context/ ./context/
COPY skills-manifest.yaml .

# HF Spaces uses port 7860
ENV PORT=7860
ENV PYTHONUNBUFFERED=1

EXPOSE 7860

CMD ["uvicorn", "production.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
