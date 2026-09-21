FROM apache/airflow:2.10.5-python3.11

# Keep application dependencies separate from Airflow's pinned dependencies.
# Tasks use this interpreter; the scheduler/webserver keep the original one.
USER root
RUN mkdir -p /opt/project-venv && chown airflow:0 /opt/project-venv
USER airflow
COPY --chown=airflow:0 requirements.txt /tmp/project-requirements.txt
RUN python -m venv /opt/project-venv \
    && /opt/project-venv/bin/python -m pip install --no-cache-dir -r /tmp/project-requirements.txt
ENV APPLICATION_PYTHON=/opt/project-venv/bin/python
ENV APPLICATION_ROOT=/opt/airflow/project

# Docker initializes the named metadata volume from this writable directory.
USER root
RUN mkdir -p /opt/airflow/state && chown airflow:0 /opt/airflow/state && chmod 0775 /opt/airflow/state
USER airflow
