import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fxpay.settings")

app = Celery("fxpay")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
# Project-level tasks package (not a Django app).
app.conf.imports = ["tasks.withdrawals"]
