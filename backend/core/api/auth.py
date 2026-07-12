from rest_framework import authentication, exceptions
from core.models import Agent

class AgentTokenAuthentication(authentication.BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if not auth_header.startswith('Token '):
            return None
        raw_token = auth_header.split(' ')[1]
        # Ищем агента, сравнивая токен через check_token (поддерживает хешированные токены)
        for agent in Agent.objects.filter(is_active=True):
            if agent.check_token(raw_token):
                return (None, agent)
        raise exceptions.AuthenticationFailed('Неверный токен агента')
