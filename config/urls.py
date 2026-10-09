from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from matching.views import CaseViewSet, LoginView
router = DefaultRouter()
router.register('cases', CaseViewSet, basename='case')
urlpatterns = [path('health/', lambda request: JsonResponse({'status': 'ok', 'service': 'HesabCheck'})),
    path('admin/', admin.site.urls), path('api/auth/token/', LoginView.as_view()),
    path('api/', include(router.urls)), path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'))]
