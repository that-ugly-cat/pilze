FROM python:3.12-slim

WORKDIR /app

# I wheel di rasterio/geopandas/pyproj includono GDAL/PROJ/GEOS ma linkano libexpat
# di sistema, assente in slim → va installata (altrimenti "import rasterio" fallisce).
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Codice (i dati grezzi restano fuori: montati come volume /app/data)
COPY engine ./engine
COPY gis ./gis
COPY bot ./bot
COPY webapp ./webapp
COPY config ./config
COPY profiles ./profiles

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# default = web app; bot e poller sovrascrivono command nel compose
CMD ["uvicorn", "webapp.app:app", "--host", "0.0.0.0", "--port", "8000"]
