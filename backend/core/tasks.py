from celery import shared_task

@shared_task
def send_incident_alerts(incident_id):
    pass
from celery import shared_task
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta
from .models import Agent, FileRecord, Incident, TriggerWord, SiteSettings

@shared_task
def send_daily_report():
    """Отправляет ежедневный отчёт на email администраторов"""
    settings = SiteSettings.load()
    recipients = [e.strip() for e in settings.report_recipients.split(',') if e.strip()]
    if not recipients:
        return "Нет получателей"

    now = timezone.now()
    today = now.date()
    yesterday = today - timedelta(days=1)

    agents_total = Agent.objects.count()
    agents_online = Agent.objects.filter(last_heartbeat__gte=now - timedelta(minutes=5)).count()
    files_24h = FileRecord.objects.filter(created_at__date=today).count()
    incidents_24h = Incident.objects.filter(detected_at__date=today).count()
    new_incidents = Incident.objects.filter(status='new').count()

    # Топ-5 триггеров за сутки
    top_triggers = Incident.objects.filter(detected_at__date=today) \
                        .values('trigger_word__word') \
                        .annotate(count=models.Count('id')).order_by('-count')[:5]
    triggers_str = "\n".join([f"{t['trigger_word__word']}: {t['count']}" for t in top_triggers])

    subject = f"[AFM] Ежедневный отчёт за {today.strftime('%d.%m.%Y')}"
    message = f"""Ежедневный отчёт Astra File Monitor

Дата: {today.strftime('%d.%m.%Y')}
Агентов онлайн: {agents_online} из {agents_total}
Новых файлов за сутки: {files_24h}
Инцидентов за сутки: {incidents_24h}
Открытых инцидентов: {new_incidents}

Топ-5 триггеров:
{triggers_str}

С уважением,
AFM System
"""
    send_mail(subject, message, None, recipients, fail_silently=True)
    return f"Отчёт отправлен {len(recipients)} получателям"
