FROM python:3.13-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fakepay ./fakepay
RUN useradd --system --uid 10001 fakepay
USER fakepay
EXPOSE 8001
CMD ["uvicorn", "fakepay.app:app", "--host", "0.0.0.0", "--port", "8001"]
