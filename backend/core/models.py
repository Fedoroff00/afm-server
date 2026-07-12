from django.db import models
from django.contrib.auth.models import User
from django.contrib.auth.hashers import make_password, check_password
from django.contrib.postgres.search import SearchVectorField
from django.utils import timezone
from datetime import timedelta
import uuid

class Agent(models.Model):
    hostname = models.CharField(max_length=255)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    token = models.CharField(max_length=128, unique=True, default=uuid.uuid4)
    version = models.CharField(max_length=20, default='1.0')
    is_active = models.BooleanField(default=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    total_files = models.IntegerField(default=0)
    pending_full_index = models.BooleanField(default=False)
    pending_restart = models.BooleanField(default=False)
    pending_update = models.BooleanField(default=False)
    last_restart = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.token and not self.token.startswith('pbkdf2_sha256$'):
            self.token = make_password(self.token)
        super().save(*args, **kwargs)

    def check_token(self, raw_token):
        """Проверяет, соответствует ли переданный токен хранимому хешу."""
        return check_password(raw_token, self.token)

    def is_online(self):
        if self.last_heartbeat is None:
            return False
        return timezone.now() - self.last_heartbeat < timedelta(minutes=5)

    def __str__(self):
        return f"{self.hostname} ({self.ip_address})"

class TriggerWord(models.Model):
    word = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    case_sensitive = models.BooleanField(default=False)
    substring_match = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.word

class FileRecord(models.Model):
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE, related_name='files')
    file_path = models.TextField()
    file_name = models.CharField(max_length=255)
    file_size = models.BigIntegerField(default=0)
    file_mtime = models.DateTimeField()
    content_text = models.TextField(blank=True)
    search_vector = SearchVectorField(null=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('agent', 'file_path')
        indexes = [
            models.Index(fields=['agent', 'file_path']),
            models.Index(fields=['file_name']),
        ]

class Incident(models.Model):
    STATUS_CHOICES = [
        ('new', 'Новый'),
        ('ack', 'Подтверждён'),
        ('resolved', 'Решён'),
    ]
    agent = models.ForeignKey(Agent, on_delete=models.CASCADE)
    trigger_word = models.ForeignKey(TriggerWord, on_delete=models.SET_NULL, null=True)
    file_path = models.TextField()
    file_name = models.CharField(max_length=255)
    file_owner = models.CharField(max_length=100, blank=True)
    context = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='new')
    assigned_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    comment = models.TextField(blank=True)
    detected_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-detected_at']

class SiteSettings(models.Model):
    logo = models.ImageField(upload_to='site/', blank=True, null=True, verbose_name='Логотип')
    site_title = models.CharField(max_length=100, default='Astra File Monitor', verbose_name='Название сайта')

    class Meta:
        verbose_name = 'Настройки сайта'
        verbose_name_plural = 'Настройки сайта'

    def save(self, *args, **kwargs):
        self.id = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, created = cls.objects.get_or_create(id=1)
        return obj

class AgentConfig(models.Model):
    agent = models.OneToOneField(Agent, on_delete=models.CASCADE, related_name='config')
    scan_directories = models.TextField(default='/home', help_text='Список директорий через запятую')
    exclude_patterns = models.TextField(blank=True, help_text='Шаблоны исключений (regex), по одному на строку')
    max_file_size_mb = models.IntegerField(default=50)
    allowed_extensions = models.TextField(default='.txt,.log,.md,.csv,.json,.xml,.yaml,.yml,.ini,.cfg,.conf,.html,.odt,.docx,.rtf,.pdf')
    scan_interval_minutes = models.IntegerField(default=60)
    enabled = models.BooleanField(default=True)

    def __str__(self):
        return f"Config for {self.agent.hostname}"
