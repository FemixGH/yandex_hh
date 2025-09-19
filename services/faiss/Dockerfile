FROM python:3.11-slim
WORKDIR /app
COPY app.py .
RUN apt-get update && apt-get install -y build-essential libopenblas-dev
RUN pip install fastapi uvicorn requests pydantic numpy faiss-cpu
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
