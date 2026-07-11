from rest_framework import authentication, exceptions
from core.models import Agent

class AgentTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Token '):
            return None
        token = auth_header.split(' ')[1]
        try:
            agent = Agent.objects.get(token=token, is_active=True)
        except Agent.DoesNotExist:
            raise exceptions.AuthenticationFailed('Неверный токен агента')
        # Возвращаем (None, agent) – тогда request.user будет AnonymousUser,
        # а request.auth – наш агент
        return (None, agent)
