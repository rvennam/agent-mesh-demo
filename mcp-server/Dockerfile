FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp>=1.12,<2" "uvicorn>=0.30"
COPY server.py .
ENV PORT=8000
EXPOSE 8000
USER 65532:65532
CMD ["python", "server.py"]
