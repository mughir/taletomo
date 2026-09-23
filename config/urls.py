from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from taletomo.web import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", views.TaleTomoLoginView.as_view(), name="login"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("taletomo.web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
