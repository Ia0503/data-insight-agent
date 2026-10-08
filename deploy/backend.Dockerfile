FROM python@sha256:3d5ed973e45820f5ba5e46bd065bd88b3a504ff0724d85980dcd05eab361fcf4

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 HOME=/tmp HF_HOME=/tmp/huggingface OMP_NUM_THREADS=2
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN python -m pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY test/data/business/ test/data/business/
RUN python -m pip install --no-deps --no-cache-dir -e ./backend \
    && useradd --uid 1000 --create-home app \
    && mkdir -p runtime/uploads runtime/models \
    && chown -R app:app runtime
USER app
EXPOSE 8000
CMD ["python", "backend/run_server.py"]
