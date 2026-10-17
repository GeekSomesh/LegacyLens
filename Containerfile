# LegacyLens API for IBM LinuxONE (s390x). Build and run with podman:
#   podman build -t legacylens -f Containerfile .
#   podman run -d --name legacylens --network host \
#     -v ~/data:/data:ro -e LEGACYLENS_DATA="/data/default of credit card clients.xls" legacylens
# --network host lets the API reach the Granite llama-server on 127.0.0.1:8080.
FROM docker.io/library/ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gnucobol3 libzdnn0 python3 python3-venv python3-pip \
        python3-numpy python3-pandas python3-sklearn python3-onnx ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m venv --system-site-packages /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt

RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --chown=app legacylens/ legacylens/
COPY --chown=app cobol/ cobol/
COPY --chown=app models/ models/
COPY --chown=app results/ results/
COPY --chown=app scripts/ scripts/
RUN mkdir -p /app/build && chown app /app/build
USER app

ENV LEGACYLENS_LLM_URL=http://127.0.0.1:8080 \
    PYTHONWARNINGS=ignore
EXPOSE 8000
CMD ["/opt/venv/bin/uvicorn", "legacylens.api:app", "--host", "0.0.0.0", "--port", "8000"]
