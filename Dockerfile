FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock .
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements.lock
# estrategia.py is the private entry engine: required by the service, never by a customer.
COPY agente.py estrategia.py config.txt ./
RUN useradd --uid 10001 --create-home qts && mkdir /app/estado_servidor && chown -R qts:qts /app
USER qts
EXPOSE 8000
CMD ["python", "agente.py", "--servir"]
