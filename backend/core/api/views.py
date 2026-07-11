from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from core.models import FileRecord, Incident, TriggerWord, AgentConfig
from .serializers import FileRecordSerializer
from .auth import AgentTokenAuthentication
from django.db.models import Q

class HeartbeatView(APIView):
    authentication_classes = [AgentTokenAuthentication]
    permission_classes = []

    def post(self, request):
        agent = request.auth
        if not agent:
            return Response({'error': 'Unauthorized'}, status=401)
        agent.last_heartbeat = timezone.now()
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

        # Динамический URL deb-пакета на основе хоста запроса
        host = request.get_host()
        scheme = 'http'  # можно заменить на request.scheme, если используется HTTPS
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

            for word in triggers:
                if word.lower() in content.lower():
                    existing = Incident.objects.filter(
                        agent=agent,
                        file_path=file_path,
                        status__in=['new', 'ack']
                    ).exists()
                    if not existing:
                        Incident.objects.create(
                            agent=agent,
                            trigger_word=TriggerWord.objects.get(word=word),
                            file_path=file_path,
                            file_name=defaults['file_name'],
                            file_owner='unknown',
                            context=content[:200],
                            status='new'
                        )
                    break

        agent.total_files = FileRecord.objects.filter(agent=agent).count()
        agent.save(update_fields=['total_files'])
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
