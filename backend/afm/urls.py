from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views
from core.views import (
    dashboard, machine_list, search_view, incident_list, incident_mark,
    settings_view, add_agent, agent_created, agent_detail,
    agent_regenerate_token, agent_delete, agent_request_full_index,
    agent_restart, agent_update, agents_mass_action,
    download_agent, file_view, export_pdf,
    agent_status_api
)

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', dashboard, name='dashboard'),
    path('login/', auth_views.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('machines/', machine_list, name='machine_list'),
    path('machines/add/', add_agent, name='add_agent'),
    path('machines/created/', agent_created, name='agent_created'),
    path('machines/<int:agent_id>/', agent_detail, name='agent_detail'),
    path('machines/<int:agent_id>/regenerate-token/', agent_regenerate_token, name='agent_regenerate_token'),
    path('machines/<int:agent_id>/delete/', agent_delete, name='agent_delete'),
    path('machines/<int:agent_id>/full-index/', agent_request_full_index, name='agent_request_full_index'),
    path('machines/<int:agent_id>/restart/', agent_restart, name='agent_restart'),
    path('machines/<int:agent_id>/update/', agent_update, name='agent_update'),
    path('machines/mass-action/', agents_mass_action, name='agents_mass_action'),
    path('download-agent/', download_agent, name='download_agent'),
    path('search/', search_view, name='search'),
    path('file/<int:file_id>/', file_view, name='file_view'),
    path('export-pdf/', export_pdf, name='export_pdf'),
    path('incidents/', incident_list, name='incident_list'),
    path('incidents/<int:incident_id>/mark/<str:new_status>/', incident_mark, name='incident_mark'),
    path('settings/', settings_view, name='settings'),
    path('api/agent-status/<int:agent_id>/', agent_status_api, name='agent_status_api'),
    path('api/agent-status/<int:agent_id>/', agent_status_api, name='agent_status_api'),
    path('api/', include('core.api.urls')),
]
