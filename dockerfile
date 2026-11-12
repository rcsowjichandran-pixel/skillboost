# Use Python base image
FROM python:3.13-slim
 
# Set working directory
WORKDIR /app
 
# Install MySQL dependencies
RUN apt-get update && apt-get install -y \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
&& rm -rf /var/lib/apt/lists/*
 
# Copy requirements
COPY requirements.txt .
 
# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt
 
# Copy Django project
COPY . .
 
# Expose Django port
EXPOSE 8000
 
# Collect static assets and start the production WSGI server.
CMD ["sh", "-c", "python manage.py collectstatic --noinput && exec gunicorn skillboost.wsgi:application --bind 0.0.0.0:8000"]