FROM python:3.13-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    "fastapi>=0.115" "uvicorn>=0.30" "openai>=1.60,<3" "mcp>=1.12,<2" \
    "agentsts-core>=0.10.1" "pyjwt>=2.9" "httpx>=0.27"
COPY app.py .
COPY static ./static
ENV PORT=8080
EXPOSE 8080
USER 65532:65532
CMD ["python", "app.py"]
