FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY gnb-log-parser.py .

EXPOSE 9090

CMD ["python3", "gnb-log-parser.py"]