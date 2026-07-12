from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import IntegrityError
from django.db.models import Count, Q
from django.utils import timezone
from datetime import timedelta, datetime
from .models import Agent, Incident, TriggerWord, FileRecord, SiteSettings
import uuid, json, re, os
from django.http import FileResponse, HttpResponse
from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank

@login_required
def dashboard(request):
    try:
        now = timezone.now()
        today = now.date()
        agents_online = Agent.objects.filter(last_heartbeat__gte=now - timedelta(minutes=5)).count()
        agents_total = Agent.objects.count()
        agents_offline = agents_total - agents_online
        online_percent = round((agents_online / agents_total * 100) if agents_total > 0 else 0)
        total_files = FileRecord.objects.count()
        new_incidents = Incident.objects.filter(status='new').count()
        last_hb = Agent.objects.filter(last_heartbeat__isnull=False).order_by('-last_heartbeat').first()
        last_heartbeat = last_hb.last_heartbeat.strftime('%d.%m.%Y %H:%M') if last_hb else None
        files_24h = FileRecord.objects.filter(created_at__gte=now - timedelta(hours=24)).count()
        incidents_24h = Incident.objects.filter(detected_at__gte=now - timedelta(hours=24)).count()
        agents_new_24h = Agent.objects.filter(created_at__gte=now - timedelta(hours=24)).count()

        days, incidents_counts = [], []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            days.append(day.strftime('%d.%m'))
            incidents_counts.append(Incident.objects.filter(detected_at__date=day).count())

        file_days, file_counts = [], []
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            file_days.append(day.strftime('%d.%m'))
            file_counts.append(FileRecord.objects.filter(created_at__date=day).count())

        trigger_stats = Incident.objects.values('trigger_word__word').annotate(count=Count('id')).order_by('-count')
        trigger_labels = [item['trigger_word__word'] for item in trigger_stats if item['trigger_word__word']]
        trigger_data = [item['count'] for item in trigger_stats if item['trigger_word__word']]

        top_agents = Agent.objects.annotate(file_count=Count('files')).order_by('-file_count')[:5]
        top_agent_names = [a.hostname for a in top_agents]
        top_agent_files = [a.file_count for a in top_agents]

        ext_counts = {}
        for fr in FileRecord.objects.all():
            ext = os.path.splitext(fr.file_name)[1].lower() or "без расширения"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
        sorted_exts = sorted(ext_counts.items(), key=lambda x: x[1], reverse=True)[:7]
        file_type_labels = [item[0] for item in sorted_exts]
        file_type_data = [item[1] for item in sorted_exts]

    except Exception as e:
        agents_online = agents_offline = total_files = new_incidents = 0
        agents_total = online_percent = 0
        files_24h = incidents_24h = agents_new_24h = 0
        last_heartbeat = None
        days, incidents_counts = [], []
        file_days, file_counts = [], []
        trigger_labels, trigger_data = [], []
        top_agent_names, top_agent_files = [], []
        file_type_labels, file_type_data = [], []
        if request.user.is_superuser:
            messages.error(request, f'Ошибка получения данных: {e}')

    return render(request, 'dashboard.html', {
        'agents_online': agents_online,
        'agents_offline': agents_offline,
        'agents_total': agents_total,
        'online_percent': online_percent,
        'total_files': total_files,
        'new_incidents': new_incidents,
        'last_heartbeat': last_heartbeat,
        'incident_days': json.dumps(days),
        'incident_counts': json.dumps(incidents_counts),
        'files_24h': files_24h,
        'incidents_24h': incidents_24h,
        'agents_new_24h': agents_new_24h,
        'file_days': json.dumps(file_days),
        'file_counts': json.dumps(file_counts),
        'trigger_labels': json.dumps(trigger_labels),
        'trigger_data': json.dumps(trigger_data),
        'top_agent_names': json.dumps(top_agent_names),
        'top_agent_files': json.dumps(top_agent_files),
        'file_type_labels': json.dumps(file_type_labels),
        'file_type_data': json.dumps(file_type_data),
    })

@login_required
def machine_list(request):
    agents = Agent.objects.all()
    total = agents.count()
    online = sum(1 for a in agents if a.is_online())
    offline = total - online
    return render(request, 'machines.html', {'agents': agents, 'total': total, 'online': online, 'offline': offline})

@login_required
def agent_detail(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    days, hb_counts = [], []
    today = timezone.now().date()
    from .models import HeartbeatLog
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        days.append(day.strftime('%d.%m'))
        cnt = HeartbeatLog.objects.filter(agent=agent, timestamp__date=day).count()
        hb_counts.append(cnt)
    return render(request, 'agent_detail.html', {'agent': agent, 'days_json': json.dumps(days), 'hb_counts_json': json.dumps(hb_counts)})

@login_required
def agent_regenerate_token(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    agent.token = str(uuid.uuid4())
    agent.save()
    messages.success(request, f'Токен агента {agent.hostname} обновлён.')
    return redirect('agent_detail', agent_id=agent.id)

@login_required
def agent_delete(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    agent.delete()
    messages.success(request, f'Агент {agent.hostname} удалён.')
    return redirect('machine_list')

@login_required
def agent_request_full_index(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    agent.pending_full_index = True
    agent.save(update_fields=["pending_full_index"])
    messages.success(request, f"Запрос на полную индексацию для {agent.hostname} отправлен.")
    return redirect('agent_detail', agent_id=agent.id)

@login_required
def agent_restart(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    agent.pending_restart = True
    agent.save(update_fields=['pending_restart'])
    messages.success(request, f'Команда перезапуска для {agent.hostname} отправлена.')
    return redirect('agent_detail', agent_id=agent.id)

@login_required
def agent_update(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE core_agent ADD COLUMN IF NOT EXISTS pending_update BOOLEAN DEFAULT FALSE;")
        cursor.execute("UPDATE core_agent SET pending_update = TRUE WHERE id = %s", [agent.id])
    messages.success(request, f'Команда обновления для {agent.hostname} отправлена.')
    return redirect('agent_detail', agent_id=agent.id)

@login_required
def agents_mass_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        agent_ids = request.POST.getlist('agent_ids')
        if not agent_ids:
            messages.error(request, "Не выбрано ни одного агента.")
            return redirect('machine_list')
        agents = Agent.objects.filter(id__in=agent_ids)
        if action == 'restart':
            agents.update(pending_restart=True)
            messages.success(request, f'Команда перезапуска отправлена {agents.count()} агентам.')
        elif action == 'update':
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("ALTER TABLE core_agent ADD COLUMN IF NOT EXISTS pending_update BOOLEAN DEFAULT FALSE;")
                cursor.execute("UPDATE core_agent SET pending_update = TRUE WHERE id IN %s", [tuple(agent_ids)])
            messages.success(request, f'Команда обновления отправлена {len(agent_ids)} агентам.')
        else:
            messages.error(request, "Неизвестное действие.")
        return redirect('machine_list')
    return redirect('machine_list')

@login_required
def add_agent(request):
    if request.method == 'POST':
        hostname = request.POST.get('hostname', '').strip()
        ip = request.POST.get('ip_address', '').strip() or None
        if not hostname:
            messages.error(request, 'Хостнейм обязателен.')
            return render(request, 'add_agent.html')
        try:
            token = str(uuid.uuid4())
            agent = Agent.objects.create(hostname=hostname, ip_address=ip, token=token)
            request.session['new_agent_token'] = token
            request.session['new_agent_id'] = agent.id
            return redirect('agent_created')
        except IntegrityError:
            messages.error(request, 'Агент с таким хостнеймом уже существует.')
            return render(request, 'add_agent.html')
        except Exception as e:
            messages.error(request, f'Ошибка при создании агента: {e}')
            return render(request, 'add_agent.html')
    return render(request, 'add_agent.html')

@login_required
def agent_created(request):
    token = request.session.pop('new_agent_token', None)
    agent_id = request.session.pop('new_agent_id', None)
    if not token or not agent_id:
        messages.error(request, 'Токен уже был показан или сессия истекла.')
        return redirect('add_agent')
    agent = get_object_or_404(Agent, id=agent_id)
    return render(request, 'agent_created.html', {'created_token': token, 'agent': agent})

@login_required
def search_view(request):
    query = request.GET.get('q', '').strip()
    agent_ids = request.GET.getlist('agent')
    ext_filter = request.GET.get('ext', '').strip().lower()
    size_from = request.GET.get('size_from', '').strip()
    size_to = request.GET.get('size_to', '').strip()
    date_from = request.GET.get('date_from', '').strip()
    date_to = request.GET.get('date_to', '').strip()
    owner = request.GET.get('owner', '').strip()
    export_csv = request.GET.get('export')

    results = FileRecord.objects.select_related('agent').all()
    if query:
        search_query = SearchQuery(query, config='russian')
        q_objects = Q()
        tokens = re.findall(r'"[^"]*"|\S+', query)
        for token in tokens:
            if token.startswith('"') and token.endswith('"'):
                phrase = token[1:-1]
                q_objects &= Q(search_vector=SearchQuery(phrase, config='russian')) | Q(file_name__icontains=phrase)
            elif token.startswith('-'):
                term = token[1:]
                q_objects &= ~Q(search_vector=SearchQuery(term, config='russian')) & ~Q(file_name__icontains=term)
            else:
                q_objects &= Q(search_vector=SearchQuery(token, config='russian')) | Q(file_name__icontains=token)
        results = results.filter(q_objects).annotate(rank=SearchRank('search_vector', search_query)).order_by('-rank')

    if agent_ids: results = results.filter(agent_id__in=agent_ids)
    if ext_filter:
        if not ext_filter.startswith('.'): ext_filter = '.' + ext_filter
        results = results.filter(file_name__iendswith=ext_filter)
    if size_from:
        try: results = results.filter(file_size__gte=int(size_from) * 1024)
        except ValueError: pass
    if size_to:
        try: results = results.filter(file_size__lte=int(size_to) * 1024)
        except ValueError: pass
    if date_from:
        try: results = results.filter(file_mtime__date__gte=datetime.strptime(date_from, '%Y-%m-%d').date())
        except ValueError: pass
    if date_to:
        try: results = results.filter(file_mtime__date__lte=datetime.strptime(date_to, '%Y-%m-%d').date())
        except ValueError: pass
    if owner: results = results.filter(file_owner__icontains=owner)

    results = results[:200]

    if export_csv == 'csv':
        import csv
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="search_results.csv"'
        writer = csv.writer(response)
        writer.writerow(['File Name', 'Path', 'Agent', 'Size', 'Modified', 'Owner'])
        for f in results:
            writer.writerow([f.file_name, f.file_path, f.agent.hostname, f.file_size, f.file_mtime, f.file_owner])
        return response

    triggers = list(TriggerWord.objects.filter(is_active=True).values_list('word', flat=True))
    tokens = re.findall(r'"[^"]*"|\S+', query) if query else []
    for f in results:
        text = f.content_text
        if query:
            positions = [text.lower().find(token.strip('"').lstrip('-').lower()) for token in tokens if token.strip('"').lstrip('-')]
            positions = [p for p in positions if p != -1]
            if positions:
                first_idx = min(positions)
                start = max(0, first_idx - 60)
                end = min(len(text), first_idx + 60)
                snippet = text[start:end]
            else:
                snippet = text[:200]
        else:
            snippet = text[:200]

        for token in tokens:
            clean_token = token.strip('"').lstrip('-')
            if clean_token:
                snippet = re.sub(r'(' + re.escape(clean_token) + r')', r'<mark class="search-highlight">\1</mark>', snippet, flags=re.IGNORECASE)
        for trig in triggers:
            if trig.lower() in snippet.lower():
                snippet = re.sub(r'(' + re.escape(trig) + r')', r'<mark class="trigger-highlight">\1</mark>', snippet, flags=re.IGNORECASE)
        f.highlighted_context = snippet

    context = {
        'query': query, 'results': results,
        'agents': Agent.objects.all(),
        'selected_agents': [int(a) for a in agent_ids],
        'ext_filter': ext_filter, 'size_from': size_from, 'size_to': size_to,
        'date_from': date_from, 'date_to': date_to, 'owner': owner,
    }
    return render(request, 'search.html', context)

@login_required
def file_view(request, file_id):
    file_record = get_object_or_404(FileRecord, id=file_id)
    return render(request, 'file_view.html', {'file': file_record})

@login_required
def incident_list(request):
    incidents = Incident.objects.select_related('agent', 'trigger_word').all()[:100]
    return render(request, 'incidents.html', {'incidents': incidents})

@login_required
def incident_mark(request, incident_id, new_status):
    inc = get_object_or_404(Incident, id=incident_id)
    if new_status in dict(Incident.STATUS_CHOICES):
        inc.status = new_status
        inc.save()
        messages.success(request, f"Статус инцидента изменён на {inc.get_status_display()}.")
    return redirect('incident_list')

@login_required
def settings_view(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            word = request.POST.get('word', '').strip().lower()
            if word:
                if not TriggerWord.objects.filter(word=word).exists():
                    TriggerWord.objects.create(word=word)
                    messages.success(request, f'Слово "{word}" добавлено.')
                else:
                    messages.warning(request, 'Такое слово уже существует.')
            else:
                messages.error(request, 'Введите слово.')
        elif action == 'delete':
            trig = get_object_or_404(TriggerWord, id=request.POST.get('id'))
            trig.delete()
            messages.success(request, f'Слово "{trig.word}" удалено.')
        elif action == 'toggle':
            trig = get_object_or_404(TriggerWord, id=request.POST.get('id'))
            trig.is_active = not trig.is_active
            trig.save()
            messages.success(request, f'Слово "{trig.word}" {"активировано" if trig.is_active else "деактивировано"}.')
        return redirect('settings')
    triggers = TriggerWord.objects.all()
    return render(request, 'settings.html', {'triggers': triggers})

@login_required
def download_agent(request):
    file_path = os.path.join(settings.MEDIA_ROOT, 'packages', 'astra-monitor-agent_latest_all.deb')
    if os.path.exists(file_path):
        return FileResponse(open(file_path, 'rb'), as_attachment=True, filename='astra-monitor-agent_latest_all.deb')
    messages.error(request, 'Файл агента не найден.')
    return redirect('dashboard')

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from datetime import datetime

@login_required
def export_pdf(request):
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="afm_report.pdf"'

    now = timezone.now()
    agents_total = Agent.objects.count()
    agents_online = Agent.objects.filter(last_heartbeat__gte=now - timedelta(minutes=5)).count()
    total_files = FileRecord.objects.count()
    new_incidents = Incident.objects.filter(status='new').count()
    incidents = Incident.objects.select_related('agent', 'trigger_word').all()[:30]

    p = canvas.Canvas(response, pagesize=A4)
    width, height = A4

    p.setFont("Helvetica-Bold", 16)
    p.drawString(30, height - 40, "Astra File Monitor - Отчёт")
    p.setFont("Helvetica", 10)
    p.drawString(30, height - 55, f"Дата: {now.strftime('%d.%m.%Y %H:%M')}")

    p.drawString(30, height - 75, f"Агентов онлайн: {agents_online} из {agents_total}")
    p.drawString(30, height - 85, f"Всего файлов: {total_files}")
    p.drawString(30, height - 95, f"Новых инцидентов: {new_incidents}")

    y = height - 115
    p.setFont("Helvetica-Bold", 9)
    p.drawString(30, y, "Дата")
    p.drawString(110, y, "Агент")
    p.drawString(190, y, "Файл")
    p.drawString(300, y, "Триггер")
    p.drawString(380, y, "Статус")
    y -= 15
    p.setFont("Helvetica", 8)
    for inc in incidents:
        if y < 50:
            p.showPage()
            y = height - 40
        p.drawString(30, y, inc.detected_at.strftime('%d.%m.%Y %H:%M'))
        p.drawString(110, y, inc.agent.hostname[:20])
        p.drawString(190, y, inc.file_name[:25])
        p.drawString(300, y, inc.trigger_word.word[:20] if inc.trigger_word else '—')
        p.drawString(380, y, dict(Incident.STATUS_CHOICES).get(inc.status, '—'))
        y -= 15

    p.save()
    return response

from django.http import JsonResponse

def agent_status_api(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id)
    return JsonResponse({
        'status': agent.status,
        'scan_progress': agent.scan_progress,
        'status_message': agent.status_message,
        'is_online': agent.is_online(),
    })
