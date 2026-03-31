# ============================================================
# mock_jira/views.py
#
# Endpoint ini mensimulasikan JIRA REST API.
# Sistem utama kamu cukup hit endpoint ini,
# seolah-olah sedang menarik data dari JIRA sungguhan.
# ============================================================

from django.http import JsonResponse
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.core.paginator import Paginator
from .models import JiraMainTicket, JiraSubTicket
import json


# ── Helper: format 1 sub ticket jadi dict ───────────────────

def format_sub_ticket(sub):
    return {
        "id":                 sub.id,
        "key":                sub.issue_key,
        "fields": {
            "parent":             {"key": sub.parent_key.issue_key},
            "status":             {"name": sub.status},
            "start_date":         str(sub.start_date) if sub.start_date else None,
            "due_date":           str(sub.due_date)   if sub.due_date   else None,
            "predefined_process": sub.predefined_process,
            "cycle_time_days":    (
                (sub.due_date - sub.start_date).days
                if sub.start_date and sub.due_date else None
            ),
        }
    }


# ── Helper: format 1 main ticket jadi dict ──────────────────

def format_main_ticket(main, include_subtasks=False):
    data = {
        "id":     main.id,
        "key":    main.issue_key,
        "fields": {
            "status":           {"name": main.status},
            "created":          str(main.created),
            "package":          main.package,
            "process_required": main.process_required,
            "subtasks_count":   main.sub_tickets.count(),
        }
    }
    if include_subtasks:
        data["fields"]["subtasks"] = [
            format_sub_ticket(s) for s in main.sub_tickets.all()
        ]
    return data


# ── VIEW 1: GET semua Main Ticket ────────────────────────────
# Endpoint : GET /mock-jira/api/issues/
# Mirip    : GET https://your-jira.atlassian.net/rest/api/2/search
#
# Query params (semua opsional):
#   status      = To Do | In Progress | Done
#   package     = DSO 300 mil  (partial match)
#   start_after = 2024-10-01   (filter created >= tanggal ini)
#   start_before= 2025-03-31   (filter created <= tanggal ini)
#   page        = 1            (nomor halaman, default 1)
#   page_size   = 50           (jumlah per halaman, default 50, max 100)

class JiraIssueListView(View):

    def get(self, request):
        qs = JiraMainTicket.objects.all()

        # ── Filter ──
        status_filter  = request.GET.get('status')
        package_filter = request.GET.get('package')
        start_after    = request.GET.get('start_after')
        start_before   = request.GET.get('start_before')

        if status_filter:
            qs = qs.filter(status=status_filter)
        if package_filter:
            qs = qs.filter(package__icontains=package_filter)
        if start_after:
            qs = qs.filter(created__gte=start_after)
        if start_before:
            qs = qs.filter(created__lte=start_before)

        # ── Pagination ──
        page_size = min(int(request.GET.get('page_size', 50)), 100)
        paginator = Paginator(qs, page_size)
        page_num  = int(request.GET.get('page', 1))
        page_obj  = paginator.get_page(page_num)

        return JsonResponse({
            "total":       paginator.count,
            "page":        page_num,
            "page_size":   page_size,
            "total_pages": paginator.num_pages,
            "issues": [
                format_main_ticket(m) for m in page_obj.object_list
            ]
        })


# ── VIEW 2: GET detail 1 Main Ticket + semua Sub Ticket-nya ──
# Endpoint : GET /mock-jira/api/issues/<issue_key>/
# Mirip    : GET https://your-jira.atlassian.net/rest/api/2/issue/DEVSMETS-00001

class JiraIssueDetailView(View):

    def get(self, request, issue_key):
        try:
            main = JiraMainTicket.objects.get(issue_key=issue_key)
        except JiraMainTicket.DoesNotExist:
            return JsonResponse({"error": f"Issue {issue_key} tidak ditemukan."}, status=404)

        return JsonResponse(format_main_ticket(main, include_subtasks=True))


# ── VIEW 3: GET Sub Ticket berdasarkan parent_key ────────────
# Endpoint : GET /mock-jira/api/sub-issues/?parent_key=DEVSMETS-00001
# Mirip    : GET https://your-jira.atlassian.net/rest/api/2/search?jql=parent=DEVSMETS-00001

class JiraSubIssueListView(View):

    def get(self, request):
        # ── Filter ──
        parent_key   = request.GET.get('parent_key')
        status       = request.GET.get('status')       # opsional
        process      = request.GET.get('process')
        start_after  = request.GET.get('start_after')
        start_before = request.GET.get('start_before')

        # API menyediakan semua data, filter ditentukan oleh sistem pemanggil
        qs = JiraSubTicket.objects.select_related('parent_key').all()

        if status:
            qs = qs.filter(status=status)
        if parent_key:
            qs = qs.filter(parent_key__issue_key=parent_key)
        if process:
            qs = qs.filter(predefined_process__icontains=process)
        if start_after:
            qs = qs.filter(start_date__gte=start_after)
        if start_before:
            qs = qs.filter(start_date__lte=start_before)

        # ── Pagination ──
        page_size = min(int(request.GET.get('page_size', 50)), 200)
        paginator = Paginator(qs, page_size)
        page_num  = int(request.GET.get('page', 1))
        page_obj  = paginator.get_page(page_num)

        return JsonResponse({
            "total":       paginator.count,
            "page":        page_num,
            "page_size":   page_size,
            "total_pages": paginator.num_pages,
            "sub_issues": [
                format_sub_ticket(s) for s in page_obj.object_list
            ]
        })


# ── VIEW 4: Endpoint Sync ─────────────────────────────────────
# Endpoint : POST /mock-jira/api/sync/
# Digunakan sistem utama untuk "menarik" data dari JIRA
# Mengembalikan semua data baru sejak tanggal tertentu
#
# Body JSON:
#   { "last_sync": "2025-01-01" }   ← opsional

@method_decorator(csrf_exempt, name='dispatch')
class JiraSyncView(View):

    def post(self, request):
        try:
            body      = json.loads(request.body) if request.body else {}
            last_sync = body.get('last_sync')    # format: YYYY-MM-DD
        except json.JSONDecodeError:
            last_sync = None

        # Filter data baru sejak last_sync
        main_qs = JiraMainTicket.objects.all()
        sub_qs  = JiraSubTicket.objects.select_related('parent_key').all()

        if last_sync:
            main_qs = main_qs.filter(created__gte=last_sync)
            sub_qs  = sub_qs.filter(parent_key__created__gte=last_sync)

        main_data = [format_main_ticket(m) for m in main_qs]
        sub_data  = [format_sub_ticket(s)  for s in sub_qs]

        return JsonResponse({
            "status":        "success",
            "message":       "Data berhasil ditarik dari Mock JIRA API",
            "last_sync":     last_sync or "all",
            "total_main":    len(main_data),
            "total_sub":     len(sub_data),
            "main_tickets":  main_data,
            "sub_tickets":   sub_data,
        })