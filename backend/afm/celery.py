import os
from celery import Celery
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'afm.settings')
app = Celery('afm')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
from celery.schedules import crontab
app.conf.beat_schedule = {
    'daily-report': {
        'task': 'core.tasks.send_daily_report',
        'schedule': crontab(hour=8, minute=0),  # каждый день в 8 утра
    },
}
app.conf.timezone = 'Europe/Moscow'
