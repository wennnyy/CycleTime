# ============================================================
# mock_jira/views.py
#
# Filter utama menggunakan due_date (sesuai tujuan sistem)
# - status=Completed  → hanya sub ticket yang selesai
# - due_after / due_before → filter berdasarkan kapan proses selesai
# - cycle_time_days dihitung otomatis (bisa negatif jika error injection)
# ============================================================

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from .models import JiraMainTicket, JiraSubTicket
import json


# ── Helper: format sub ticket ───────────────────────────────
def format_sub_ticket(sub):
    if sub.start_date and sub.due_date:
        cycle_time_days = (sub.due_date - sub.start_date).days
    else:
        cycle_time_days = None

    return {
        "id"     : sub.id,
        "key"    : sub.issue_key,
        "fields" : {
            "parent"            : {"key": sub.parent_key.issue_key},
            "status"            : {"name": sub.status},
            "start_date"        : str(sub.start_date) if sub.start_date else None,
            "due_date"          : str(sub.due_date)   if sub.due_date   else None,
            "predefined_process": sub.predefined_process,
            "cycle_time_days"   : cycle_time_days,
        }
    }


# ── Helper: format main ticket ──────────────────────────────
def format_main_ticket(main, include_subtasks=False):
    data = {
        "id"     : main.id,
        "key"    : main.issue_key,
        "fields" : {
            "status"          : {"name": main.status},
            "created"         : str(main.created),
            "package"         : main.package,
            "process_required": main.process_required,
            "subtasks_count"  : main.sub_tickets.count(),
            "quantity"        : main.quantity,
        }
    }
    if include_subtasks:
        data["fields"]["subtasks"] = [
            format_sub_ticket(s) for s in main.sub_tickets.all()
        ]
    return data


# ── VIEW 1: Main Tickets ─────────────────────────────────────
class JiraIssueListView(View):
    """GET /mock-jira/api/issues/"""
    def get(self, request):
        qs = JiraMainTicket.objects.all()

        if issue_keys := request.GET.getlist('issue_key'):
            qs = qs.filter(issue_key__in=issue_keys)
        if status := request.GET.get('status'):
            qs = qs.filter(status=status)
        if package := request.GET.get('package'):
            qs = qs.filter(package__icontains=package)
        if start_after := request.GET.get('start_after'):
            qs = qs.filter(created__gte=start_after)
        if start_before := request.GET.get('start_before'):
            qs = qs.filter(created__lte=start_before)

        page_size = min(int(request.GET.get('page_size', 500)), 500)
        paginator = Paginator(qs, page_size)
        page_obj  = paginator.get_page(int(request.GET.get('page', 1)))

        return JsonResponse({
            "total"      : paginator.count,
            "page"       : page_obj.number,
            "page_size"  : page_size,
            "total_pages": paginator.num_pages,
            "issues"     : [format_main_ticket(m) for m in page_obj.object_list]
        })


# ── VIEW 2: Detail 1 Main Ticket + Subtasks ──────────────────
class JiraIssueDetailView(View):
    """GET /mock-jira/api/issues/<issue_key>/"""
    def get(self, request, issue_key):
        try:
            main = JiraMainTicket.objects.get(issue_key=issue_key)
        except JiraMainTicket.DoesNotExist:
            return JsonResponse({"error": f"Issue {issue_key} tidak ditemukan."}, status=404)

        return JsonResponse(format_main_ticket(main, include_subtasks=True))


# ── VIEW 3: Sub Tickets ──────────────────────────────────────
class JiraSubIssueListView(View):
    """GET /mock-jira/api/sub-issues/"""
    def get(self, request):
        qs = JiraSubTicket.objects.select_related('parent_key').all()

        # Filter status, parent, process
        if status := request.GET.get('status'):
            qs = qs.filter(status=status)
        if parent_key := request.GET.get('parent_key'):
            qs = qs.filter(parent_key__issue_key=parent_key)
        if process := request.GET.get('process'):
            qs = qs.filter(predefined_process__icontains=process)

        # Filter utama: due_date (kapan proses selesai)
        # Parameter: due_after / due_before
        if due_after := request.GET.get('due_after'):
            qs = qs.filter(due_date__gte=due_after)
        if due_before := request.GET.get('due_before'):
            qs = qs.filter(due_date__lte=due_before)

        page_size = min(int(request.GET.get('page_size', 500)), 500)
        paginator = Paginator(qs, page_size)
        page_obj  = paginator.get_page(int(request.GET.get('page', 1)))

        return JsonResponse({
            "total"      : paginator.count,
            "page"       : page_obj.number,
            "page_size"  : page_size,
            "total_pages": paginator.num_pages,
            "sub_issues" : [format_sub_ticket(s) for s in page_obj.object_list]
        })


# ── VIEW 4: Sync ─────────────────────────────────────────────
@method_decorator(csrf_exempt, name='dispatch')
class JiraSyncView(View):
    def post(self, request):
        try:
            body = json.loads(request.body) if request.body else {}
            last_sync = body.get('last_sync')
        except Exception:
            last_sync = None

        main_qs = JiraMainTicket.objects.all()
        sub_qs  = JiraSubTicket.objects.select_related('parent_key').all()

        if last_sync:
            main_qs = main_qs.filter(created__gte=last_sync)
            sub_qs  = sub_qs.filter(parent_key__created__gte=last_sync)

        return JsonResponse({
            "status"      : "success",
            "message"     : "Data berhasil ditarik dari Mock JIRA API",
            "last_sync"   : last_sync or "all",
            "total_main"  : main_qs.count(),
            "total_sub"   : sub_qs.count(),
            "main_tickets": [format_main_ticket(m) for m in main_qs],
            "sub_tickets" : [format_sub_ticket(s) for s in sub_qs],
        })