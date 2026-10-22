FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py web.py browser_auth.py admin_accounts.py landing.py api.py api_client.py auth_store.py create_account.py csv_export.py estimator.py forum_media.py identity.py internal_auth.py oidc.py prediction.py session_auth.py storage.py valuation.py ./
COPY assets ./assets
COPY .streamlit ./.streamlit
RUN useradd --create-home --uid 10001 homenest && mkdir -p /app/data/images && chown -R homenest:homenest /app
USER homenest
ENV HOMENEST_DATA_DIR=/app/data
EXPOSE 8501 8000
CMD ["python", "-m", "streamlit", "run", "web.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
