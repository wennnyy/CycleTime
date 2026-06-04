from django.urls import path, include
from django.views.generic import RedirectView

urlpatterns = [
    # Redirect root to main API endpoint
    path('', RedirectView.as_view(url='/mock-jira/api/', permanent=False), name='root-redirect'),
    
    # All API endpoints are under /mock-jira/ prefix
    path('mock-jira/', include('mock_jira.urls')),
]