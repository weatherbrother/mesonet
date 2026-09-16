# Cloud Run job image for the Purchase calm cards.
#
# No credentials are baked in and none are needed: on Cloud Run the job assumes
# its own service account through the metadata server, and km_drive.py picks
# that up through Application Default Credentials.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cards/ /app/cards/
WORKDIR /app/cards

# /tmp is the only writable path in the container. Nothing here is durable --
# the cards and the run record are delivered to the shared drive by km_drive,
# and anything that fails to upload is reported as a gap rather than kept.
ENTRYPOINT ["python", "run_purchase_cams.py", "--root", "/tmp/run"]
