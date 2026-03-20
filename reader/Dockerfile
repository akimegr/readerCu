FROM python:3.11-slim

WORKDIR /code

COPY requirements.txt /code/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app /code/app
COPY reader /code/reader
COPY infra /code/infra

RUN mkdir -p /code/sessions && chmod 777 /code/sessions
CMD ["python", "-m", "reader.main"]

