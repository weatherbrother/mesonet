# Deploying to Cloud Run

Hourly Purchase calm cards, rendered in a Cloud Run job and delivered to the
Mesonet shared drive.

No credentials anywhere. The job runs AS the service account, and
`km_drive.py` picks that identity up through Application Default Credentials
via the metadata server. There is no key file to store, rotate or leak. If you
downloaded a JSON key while setting the account up, delete it and revoke it in
the console -- it is pure liability now.

## Settings

    PROJECT=mesonet-508815
    REGION=us-central1
    SA=mesonet-cards@mesonet-508815.iam.gserviceaccount.com
    DRIVE=0AK41u5RcuzgCUk9PVA          # shared drive root, the 0A prefix says so

The service account must already be a **Content Manager** on the Mesonet shared
drive. That membership, not any IAM role, is what grants Drive access.

## One time

    gcloud config set project "$PROJECT"

    gcloud services enable \
      run.googleapis.com \
      cloudbuild.googleapis.com \
      artifactregistry.googleapis.com \
      cloudscheduler.googleapis.com \
      drive.googleapis.com

## Build and deploy the job

    gcloud run jobs deploy mesonet-cards \
      --source . \
      --region "$REGION" \
      --service-account "$SA" \
      --set-env-vars "DRIVE_FOLDER_ID=$DRIVE" \
      --cpu 1 --memory 1Gi \
      --task-timeout 10m \
      --max-retries 0

`--max-retries 0` is deliberate. km_fetch_run already retries every HTTP call
three times with backoff, so a task-level retry adds nothing except the chance
of a crash after the upload producing a second full card set under a different
stamp. At 24 runs a day, losing one to a transient failure costs an hour;
silently duplicating a set costs trust in the folder.

Re-run this same command to ship a code change.

## Let it be invoked, then prove it works

    gcloud run jobs add-iam-policy-binding mesonet-cards \
      --region "$REGION" \
      --member "serviceAccount:$SA" \
      --role roles/run.invoker

    gcloud run jobs execute mesonet-cards --region "$REGION" --wait

Check the summary block it printed:

    gcloud logging read \
      'resource.type=cloud_run_job AND resource.labels.job_name=mesonet-cards' \
      --limit 60 --format='value(textPayload)'

`drive  8/8 uploaded` and `status clean` means the whole chain works. A
`403` on the Drive line almost always means the service account is not a member
of the shared drive.

## Schedule it hourly

    gcloud scheduler jobs create http mesonet-cards-hourly \
      --location "$REGION" \
      --schedule "0 * * * *" \
      --time-zone "America/Chicago" \
      --uri "https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/mesonet-cards:run" \
      --http-method POST \
      --oauth-service-account-email "$SA"

There is no `--schedule` flag on `gcloud run jobs deploy`; scheduling is always
a separate Cloud Scheduler job.

## What it costs

Nothing, at this volume. Cloud Run jobs include 240,000 vCPU-seconds and
450,000 GiB-seconds free per month. About 720 runs a month at roughly 90
seconds and 1 vCPU / 1 GiB is near 65,000 vCPU-seconds and 65,000 GiB-seconds,
comfortably inside both. Cloud Scheduler gives 3 free jobs per billing account
and this uses one. The only line that can tip into cents is Artifact Registry,
free to 0.5 GB, against an image around 300 MB -- so set a cleanup policy
keeping the most recent image or two and even that stays free.

## Where output lands

    <shared drive>/YYYY-MM-DD/20260916_1400_01_ballard.png
                              ...
                              manifest.md
                              km_data.json

The day folder is derived from the run folder's name, not from the clock, so a
run that starts at 23:59:58 cannot file itself under tomorrow. `km_drive.py`
looks for the day folder before creating it, because Drive allows duplicate
names and a create-instead-of-find at 24 runs a day compounds quickly.

## Volume

Twenty-four runs a day is roughly 192 files and 190 MB per day folder, about
5.7 GB a month. Shared drive storage is pooled Workspace storage, so watch it
if the pipeline ever grows more stations.
