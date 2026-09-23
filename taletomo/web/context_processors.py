from taletomo.planning.models import Project
from taletomo.providers.models import ProviderConfig


def taletomo_context(request):
    """Provides global navigation and active provider info."""
    active_provider = None
    if getattr(request, "user", None) and request.user.is_authenticated:
        active_provider = ProviderConfig.objects.filter(user=request.user, is_active=True).first()

    return {
        "active_provider": active_provider,
        "app_name": "TaleTomo",
        "app_tagline": "Grow a premise into a world.",
    }
