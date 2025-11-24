# Use Python 3.11 slim image
FROM python:3.11-slim

# Install system dependencies
RUN RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    wget \
    libatlas-base-dev \
    libblas-dev \
    liblapack-dev \
    gfortran \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*


# Set work directory
WORKDIR /app

# Install Python dependencies first (so Docker can cache better)
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . /app/

# Expose internal app port
EXPOSE 8000

# Command - overridden by docker-compose (gunicorn is run from compose)
CMD ["python", "app.py"]
