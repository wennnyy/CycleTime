# ============================================================
# mock_jira/views.py
#
# API Views Layer - HANYA handle HTTP request/response
# Semua business logic ada di services.py
# 
# Filter utama menggunakan due_date (sesuai tujuan sistem)
# - status=Completed  → hanya sub ticket yang selesai
# - due_after / due_before → filter berdasarkan kapan proses selesai
# - cycle_time_days dihitung otomatis (bisa negatif jika error injection)
# ============================================================

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator


from .services import (
    fetch_main_tickets,
    fetch_main_ticket_detail,
    fetch_sub_tickets,
    fetch_sub_tickets_date_range,
)


# ── UNIFIED ROOT API ─────────────────────────────────────────
# Merged health check and API documentation into single endpoint
class ApiRootView(View):
    """
    GET / or /mock-jira/api/
    
    Root endpoint - provides:
    - Health check status (server is running)
    - Available endpoints
    - API documentation
    """
    def get(self, request):
        return JsonResponse({
            "status": "ok",
            "message": "Mock JIRA API Server is running",
            "version": "1.0",
            
            "available_endpoints": {
                "issues_list": "http://127.0.0.1:8001/mock-jira/api/issues/",
                "issue_detail": "http://127.0.0.1:8001/mock-jira/api/issues/{issue_key}/",
                "sub_issues": "http://127.0.0.1:8001/mock-jira/api/sub-issues/",
                "sub_issues_range": "http://127.0.0.1:8001/mock-jira/api/sub-issues/range/",
            },
            
            "documentation": {
                "issues_list": "GET /mock-jira/api/issues/ - List all main tickets with pagination",
                "issue_detail": "GET /mock-jira/api/issues/{issue_key}/ - Get detail of specific main ticket with subtasks",
                "sub_issues": "GET /mock-jira/api/sub-issues/ - List all sub tickets with filtering",
                "sub_issues_range": "GET /mock-jira/api/sub-issues/range/ - Get earliest and latest due_date for completed sub-tickets",
            }
        })


# ── VIEW 1: Main Tickets ─────────────────────────────────────
class JiraIssueListView(View):
    """
    GET /mock-jira/api/issues/
    
    Fetch main tickets dengan pagination dan filtering
    
    Query Parameters:
        - issue_key: list - filter by issue keys
        - status: str - filter by status
        - package: str - filter by package name
        - start_after: str - filter created >= date
        - start_before: str - filter created <= date
        - page: int - page number (default 1)
        - page_size: int - items per page (default 500, max 500)
    """
    def get(self, request):
        # Extract filters dari request parameters
        filters = {
            'issue_keys': request.GET.getlist('issue_key'),
            'status': request.GET.get('status'),
            'package': request.GET.get('package'),
            'start_after': request.GET.get('start_after'),
            'start_before': request.GET.get('start_before'),
            'page': request.GET.get('page', 1),
            'page_size': request.GET.get('page_size', 500),
        }
        
        # Delegate ke service layer
        result = fetch_main_tickets(filters)
        
        return JsonResponse(result)


# ── VIEW 2: Detail 1 Main Ticket + Subtasks ──────────────────
class JiraIssueDetailView(View):
    """
    GET /mock-jira/api/issues/<issue_key>/
    
    Fetch detail main ticket beserta subtasks
    """
    def get(self, request, issue_key):
        try:
            # Delegate ke service layer
            result = fetch_main_ticket_detail(issue_key)
            return JsonResponse(result)
        except Exception as e:
            return JsonResponse(
                {"error": f"Issue {issue_key} tidak ditemukan."},
                status=404
            )


# ── VIEW 3: Sub Tickets ──────────────────────────────────────
class JiraSubIssueListView(View):
    """
    GET /mock-jira/api/sub-issues/
    
    Fetch sub tickets dengan pagination dan filtering
    
    Query Parameters:
        - status: str - filter by status
        - parent_key: str - filter by parent issue key
        - process: str - filter by process name
        - due_after: str - filter due_date >= date (MAIN FILTER)
        - due_before: str - filter due_date <= date (MAIN FILTER)
        - page: int - page number (default 1)
        - page_size: int - items per page (default 500, max 500)
    """
    def get(self, request):
        # Extract filters dari request parameters
        filters = {
            'status': request.GET.get('status'),
            'parent_key': request.GET.get('parent_key'),
            'process': request.GET.get('process'),
            'due_after': request.GET.get('due_after'),
            'due_before': request.GET.get('due_before'),
            'page': request.GET.get('page', 1),
            'page_size': request.GET.get('page_size', 500),
        }
        
        # Delegate ke service layer
        result = fetch_sub_tickets(filters)
        
        return JsonResponse(result)


# ── VIEW 4: Sub Issue Date Range ─────────────────────────────
class JiraSubIssueDateRangeView(View):
    """
    GET /mock-jira/api/sub-issues/range/
    
    Fetch earliest dan latest due_date untuk sub-tickets
    yang sudah completed
    """
    def get(self, request):
        # Delegate ke service layer
        result = fetch_sub_tickets_date_range()
        
        return JsonResponse(result)