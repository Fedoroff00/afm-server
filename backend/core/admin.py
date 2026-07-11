from django.contrib import admin
from .models import Agent, TriggerWord, FileRecord, Incident, SiteSettings

@admin.register(SiteSettings)
class SiteSettingsAdmin(admin.ModelAdmin):
    fields = ('logo', 'site_title')

admin.site.register(Agent)
admin.site.register(TriggerWord)
admin.site.register(FileRecord)
admin.site.register(Incident)
