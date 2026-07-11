from rest_framework import serializers
from core.models import FileRecord, Incident

class FileRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = FileRecord
        fields = ['file_path', 'file_name', 'file_size', 'file_mtime', 'content_text']

class IncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Incident
        fields = ['file_path', 'file_name', 'file_owner', 'context', 'trigger_word']
