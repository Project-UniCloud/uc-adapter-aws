FROM python:3.11-slim

RUN useradd -r -u 1001 adapteruser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R adapteruser:adapteruser /app
USER adapteruser

EXPOSE 50051

ENTRYPOINT ["python", "main.py"]
