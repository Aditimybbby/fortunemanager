FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1 DASHBOARD_HOST=0.0.0.0 DATA_DIR=/data
RUN useradd --create-home fortune && mkdir /data && chown fortune:fortune /data
ENTRYPOINT ["python", "/app/docker-entrypoint.py"]
EXPOSE 8080
CMD ["python", "main.py"]
