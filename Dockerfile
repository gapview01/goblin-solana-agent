FROM python:3.10-slim

WORKDIR /app
COPY . .

RUN pip install --upgrade pip
# add openai + requests so imports don’t crash
RUN pip install flask gunicorn openai requests

EXPOSE 8080
CMD ["gunicorn", "--bind", ":8080", "app:app"]