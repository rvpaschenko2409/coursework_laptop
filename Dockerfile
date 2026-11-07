FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/tmp/matplotlib OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
WORKDIR /app
COPY requirements.txt requirements_database.txt requirements_web.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt -r requirements_database.txt -r requirements_web.txt
COPY alembic.ini web_alembic.ini ./
COPY db_migrations db_migrations
COPY web_migrations web_migrations
COPY sql sql
COPY scripts scripts
COPY decision_core decision_core
COPY decision_store decision_store
COPY web_app web_app
COPY data data
COPY results results
COPY models/ridge.joblib models/random_forest.joblib models/
RUN mkdir -p models/web && chown 10001:10001 models/web
USER 10001:10001
