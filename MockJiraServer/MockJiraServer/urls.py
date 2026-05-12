from django.urls import path, include

urlpatterns = [
    path('mock-jira/', include('mock_jira.urls')),
]