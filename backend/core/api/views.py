from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.contrib.postgres.search import SearchQuery
from core.models import FileRecord, Incident, TriggerWord, AgentConfig, HeartbeatLog
from .auth import AgentTokenAuthentication

class HeartbeatView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = []

    def post(self, request):
        agent = request.auth
        if not agent:
            return Response({'error': 'Unauthorized'}, status=401)
        agent.last_heartbeat = timezone.now()
        agent.status = request.data.get('status', agent.status)
        agent.scan_progress = request.data.get('scan_progress', agent.scan_progress)
        agent.status_message = request.data.get('status_message', agent.status_message)[:100]
        HeartbeatLog.objects.create(agent=agent)
        command = None
        if agent.pending_full_index:
            command = 'full_index'
            agent.pending_full_index = False
        elif agent.pending_restart:
            command = 'restart'
            agent.pending_restart = False
            agent.last_restart = timezone.now()
        elif getattr(agent, 'pending_update', False):
            command = 'update'
            agent.pending_update = False
        agent.save()
        triggers = list(TriggerWord.objects.filter(is_active=True).values_list('word', flat=True))
        config, _ = AgentConfig.objects.get_or_create(agent=agent)
        config_data = {
            'scan_directories': config.scan_directories,
            'exclude_patterns': config.exclude_patterns,
            'max_file_size_mb': config.max_file_size_mb,
            'allowed_extensions': config.allowed_extensions,
            'scan_interval_minutes': config.scan_interval_minutes,
            'enabled': config.enabled,
        }
        host = request.get_host()
        scheme = 'http'
        download_url = f'{scheme}://{host}/media/packages/astra-monitor-agent_latest_all.deb'
        response_data = {
            'status': 'ok',
            'triggers': triggers,
            'config': config_data,
            'server_version': '1.0.0',
            'min_agent_version': '1.0.0',
            'download_url': download_url
        }
        if command:
            response_data['command'] = command
        return Response(response_data)

class FileUploadView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = []

    def post(self, request):
        agent = request.auth
        if not agent:
            return Response({'error': 'Unauthorized'}, status=401)
        files = request.data.get('files', [])
        created_count = 0
        triggers = list(TriggerWord.objects.filter(is_active=True).values_list('word', flat=True))

        for f in files:
            file_path = f.get('file_path')
            if not file_path:
                continue
            mtime = f.get('file_mtime')
            content = f.get('content_text', '')
            defaults = {
                'file_name': f.get('file_name', ''),
                'file_size': f.get('file_size', 0),
                'content_text': content[:10000],
            }
            if mtime:
                try:
                    defaults['file_mtime'] = parse_datetime(mtime)
                except:
                    pass
            obj, created = FileRecord.objects.update_or_create(
                agent=agent,
                file_path=file_path,
                defaults=defaults
            )
            if created:
                created_count += 1

            # Проверка триггеров: сначала быстрый поиск подстроки, затем русский стемминг
            detected = None
            for word in triggers:
                # Простая проверка
                if word.lower() in content.lower() or content.lower() in word.lower():
                    detected = word
                    break
                # Если не сработало и слово длиннее 2 символов – попробуем через SearchQuery
                if len(word) > 2:
                    try:
                        from django.contrib.postgres.search import SearchQuery, SearchRank
                        # Создаём временный SearchVector для этого контента
                        from django.contrib.postgres.search import SearchVector
                        vec = SearchVector('content_text', config='russian')
                        # Проверяем, есть ли совпадение (без сохранения)
                        # Выполним запрос к только что созданному FileRecord
                        match = FileRecord.objects.annotate(
                            search=vec
                        ).filter(
                            pk=obj.pk,
                            search=SearchQuery(word, config='russian')
                        ).exists()
                        if match:
                            detected = word
                            break
                    except:
                        pass

            if detected:
                existing = Incident.objects.filter(
                    agent=agent,
                    file_path=file_path,
                    trigger_word__word=detected
                ).exists()
                if not existing:
                    Incident.objects.create(
                        agent=agent,
                        trigger_word=TriggerWord.objects.get(word=detected),
                        file_path=file_path,
                        file_name=defaults['file_name'],
                        file_owner='unknown',
                        context=content[:200],
                        status='new'
                    )

        agent.total_files = FileRecord.objects.filter(agent=agent).count()
        if agent.status == 'uploading':
            agent.status = 'idle'
            agent.status_message = ''
        agent.save(update_fields=['total_files', 'status', 'status_message'])
        return Response({'received': created_count}, status=status.HTTP_201_CREATED)

class IncidentReportView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = []

    def post(self, request):
        agent = request.auth
        if not agent:
            return Response({'error': 'Unauthorized'}, status=401)
        data = request.data
        trigger_word_str = data.get('trigger_word')
        trigger = TriggerWord.objects.filter(word=trigger_word_str, is_active=True).first()
        incident = Incident.objects.create(
            agent=agent,
            trigger_word=trigger,
            file_path=data.get('file_path', ''),
            file_name=data.get('file_name', ''),
            file_owner=data.get('file_owner', ''),
            context=data.get('context', ''),
        )
        return Response({'id': incident.id}, status=status.HTTP_201_CREATED)

class AgentFileContentView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = []

    def get(self, request, file_id):
        agent = request.auth
        if not agent:
            return Response({'error': 'Unauthorized'}, status=401)
        try:
            file_record = FileRecord.objects.get(id=file_id, agent=agent)
        except FileRecord.DoesNotExist:
            return Response({'error': 'File not found'}, status=404)
        try:
            from django.http import StreamingHttpResponse
            def file_stream():
                with open(file_record.file_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            response = StreamingHttpResponse(file_stream(), content_type='application/octet-stream')
            response['Content-Disposition'] = f'attachment; filename="{file_record.file_name}"'
            return response
        except FileNotFoundError:
            return Response({'error': 'File not available on server'}, status=404)

class VersionView(APIView):
    permission_classes = []
    def get(self, request):
        host = request.get_host()
        scheme = 'http'
        download_url = f'{scheme}://{host}/media/packages/astra-monitor-agent_latest_all.deb'
        return Response({
            'server_version': '1.0.0',
            'min_agent_version': '1.0.0',
            'download_url': download_url
        })
