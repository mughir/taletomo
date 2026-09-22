from taletomo.planning.models import Project
from taletomo.providers.models import ProviderConfig


def taletomo_context(request):
    """Provides global navigation and active provider info."""
    user = request.user if request.user.is_authenticated else None
    active_provider = ProviderConfig.objects.filter(is_active=True).first()
    return {
        "active_provider": active_provider,
        "app_name": "TaleTomo",
        "app_tagline": "Grow a premise into a world.",
    }
