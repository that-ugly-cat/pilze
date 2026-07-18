FROM python:3.12-slim

WORKDIR /app

# rasterio/geopandas/pyproj arrivano da wheel con GDAL/PROJ/GEOS inclusi → no lib di sistema.
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
