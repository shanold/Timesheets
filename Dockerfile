FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data /app/static/uploads
ENV DATABASE_PATH=/data/timesheet.db
EXPOSE 8080
CMD ["waitress-serve", "--listen=0.0.0.0:8080", "app:app"]
