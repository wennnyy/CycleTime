from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('main.urls')), # TANPA namespace dulu
    path('mock-jira/', include('mock_jira.urls')),
]
