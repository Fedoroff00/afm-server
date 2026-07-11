from django.urls import path
from . import views

urlpatterns = [
    path('agent/heartbeat/', views.HeartbeatView.as_view(), name='agent-heartbeat'),
    path('agent/files/', views.FileUploadView.as_view(), name='agent-files'),
    path('agent/incident/', views.IncidentReportView.as_view(), name='agent-incident'),
    path('agent/file-content/<int:file_id>/', views.AgentFileContentView.as_view(), name='agent-file-content'),
    path('version/', views.VersionView.as_view(), name='version'),
]
