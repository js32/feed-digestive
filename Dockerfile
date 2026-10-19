FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/data/hf
WORKDIR /srv
COPY requirements.txt .
# WITH_LAYA=1 installiert den lokalen Laya-Vorfilter (PyTorch CPU-Build, ca. +1 GB)
ARG WITH_LAYA=0
# WITH_EMBEDDINGS=1 installiert lokale Embeddings (fastembed/ONNX, ca. +200 MB, Modell landet in /data)
ARG WITH_EMBEDDINGS=0
RUN pip install --no-cache-dir -r requirements.txt \
 && if [ "$WITH_EMBEDDINGS" = "1" ]; then pip install --no-cache-dir "fastembed==0.7.4"; fi \
 && if [ "$WITH_LAYA" = "1" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
      && pip install --no-cache-dir "laya==0.4.11"; \
    fi \
 && useradd -u 1000 -m digest && mkdir -p /data /config && chown digest /data
COPY app ./app
USER digest
EXPOSE 8080
HEALTHCHECK --interval=5m --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "app", "serve"]
