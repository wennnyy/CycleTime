# ============================================================
# mock_jira/urls.py
# ============================================================

from django.urls import path
from . import views

urlpatterns = [

    # GET  /mock-jira/api/issues/
    # Ambil semua main ticket (support filter & pagination)
    path('api/issues/', views.JiraIssueListView.as_view(), name='jira-issues'),

    # GET  /mock-jira/api/issues/DEVSMETS-00001/
    # Detail 1 main ticket + semua sub ticket-nya
    path('api/issues/<str:issue_key>/', views.JiraIssueDetailView.as_view(), name='jira-issue-detail'),

    # GET  /mock-jira/api/sub-issues/?parent_key=DEVSMETS-00001
    # Ambil sub ticket (support filter & pagination)
    path('api/sub-issues/', views.JiraSubIssueListView.as_view(), name='jira-sub-issues'),

    # POST /mock-jira/api/sync/
    # Tarik semua data (atau data baru sejak last_sync)
    path('api/sync/', views.JiraSyncView.as_view(), name='jira-sync'),
]