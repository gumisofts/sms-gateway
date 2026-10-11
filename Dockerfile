FROM python:3.11.4-slim-buster

WORKDIR /app

COPY . .

RUN apt-get update && apt-get upgrade -y && apt install -y\
    gcc libpq-dev --no-install-recommends\
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip && pip install  -r requirements.txt


CMD ["gunicorn","core.wsgi:application","-b 0.0.0.0:8000","--timeout","3600"]
