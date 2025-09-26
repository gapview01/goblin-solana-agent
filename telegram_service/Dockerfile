FROM mirror.gcr.io/library/python:3.12-slim
WORKDIR /app
COPY telegram_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .                                   # copies planner/ into image
ENV PORT=8080
CMD ["python", "telegram_service/server.py"]