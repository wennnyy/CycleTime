from functools import wraps
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.db.models import Avg, Count, Q, Min, Max
from django.core.paginator import Paginator
from main.models import User, RawTicket, SyncLog, FlagHistory, ErrorTicket
from main.process_groups import enrich_ticket
from datetime import date, timedelta, datetime
import requests


# ======================================================
# HELPER: ROLE DECORATOR
# ======================================================
def role_required(role_name):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            if request.user.role != role_name:
                return HttpResponseForbidden("⛔ Anda tidak memiliki akses ke halaman ini")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator

#
def get_published_syncs():
    """Hanya data yang sudah di-publish via tombol Create Dashboard"""
    return SyncLog.objects.filter(status='published').values_list('id', flat=True)

# ======================================================
# AUTHENTICATION
# ======================================================
def login_view(request):
    if request.user.is_authenticated:
        return redirect_by_role(request.user)

    # Flush semua messages sisa di session agar tidak bocor ke halaman lain
    storage = messages.get_messages(request)
    list(storage)
    storage.used = True

    error = None
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect_by_role(user)
        else:
            error = "Username atau password salah"

    return render(request, 'main/auth/login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def redirect_by_role(user):
    if user.role == 'admin':
        return redirect('dashboard_admin')
    elif user.role == 'management':
        return redirect('dashboard_management')
    else:
        return redirect('dashboard_staff')


# ======================================================
# DASHBOARD ADMIN
# ======================================================

def get_dashboard_filter_options():
    """Ambil semua nilai unik untuk filter dropdown di dashboard."""
    from main.process_groups import PROCESS_GROUP, PACKAGE_PLATFORM
    
    published = get_published_syncs()

    years_qs = RawTicket.objects.filter(
        parent_key__isnull=False,
        start_date__isnull=False,
        cycle_time__isnull=False,
        sync_log__in=published,
    ).dates('start_date', 'year')

    return {
        'all_years':     sorted(set(str(d.year) for d in years_qs)),
        'all_platforms': sorted(set(PACKAGE_PLATFORM.values())),
        'all_stages':    sorted(set(pg[0] for pg in PROCESS_GROUP.values())),
        'all_areas':     sorted(set(pg[1] for pg in PROCESS_GROUP.values())),
        'all_processes': sorted(set(PROCESS_GROUP.keys())),
    }

@login_required
@role_required('admin')
def dashboard_admin(request):
    published = get_published_syncs()
    
    total_main    = RawTicket.objects.filter(parent_key__isnull=True, sync_log__in=published).count()
    total_sub     = RawTicket.objects.filter(parent_key__isnull=False, sync_log__in=published).count()
    total_flagged = ErrorTicket.objects.count()
    last_sync     = SyncLog.objects.order_by('-started_at').first()

    context = {
        'username':      request.user.username,
        'role':          request.user.get_role_display(),
        'total_main':    total_main,
        'total_sub':     total_sub,
        'total_flagged': total_flagged,
        'total_users':   User.objects.count(),
        'last_sync':     last_sync,
        **get_dashboard_filter_options(),
    }
    return render(request, 'main/admin/dashboard_admin.html', context)


# ======================================================
# SYNC
# ======================================================
MOCK_JIRA_BASE = "http://localhost:8000/mock-jira/api"


def ambil_semua_halaman(url, params, timeout=30):
    hasil    = []
    page     = 1
    key_data = None

    while True:
        params['page']      = page
        params['page_size'] = 500

        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if key_data is None:
            for k in data:
                if k not in ('total', 'page', 'page_size', 'total_pages'):
                    key_data = k
                    break

        items       = data.get(key_data, [])
        hasil      += items
        total_pages = data.get('total_pages', 1)

        if page >= total_pages:
            break
        page += 1

    return hasil


@login_required
@role_required('admin')
def admin_sync(request):

    BULAN_ID = {
        1:'Januari', 2:'Februari', 3:'Maret', 4:'April',
        5:'Mei', 6:'Juni', 7:'Juli', 8:'Agustus',
        9:'September', 10:'Oktober', 11:'November', 12:'Desember'
    }

    def get_data_range_str():
        dr = RawTicket.objects.filter(start_date__isnull=False).aggregate(
            earliest=Min('start_date'), latest=Max('start_date')
        )
        e, l = dr['earliest'], dr['latest']
        if e and l:
            return f"{BULAN_ID[e.month]} {e.year} — {BULAN_ID[l.month]} {l.year}"
        return None

    # ── POST: sync / flag / unflag ───────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('action')

        # ── Jalankan sync ──
        if action == 'sync':
            start_date = request.POST.get('start_date')
            end_date   = request.POST.get('end_date')

            sync_log = SyncLog.objects.create(
                admin=request.user, started_at=timezone.now(), status='running'
            )
            total_fetched = total_processed = total_errors = 0

            try:
                all_main = ambil_semua_halaman(
                    url=f"{MOCK_JIRA_BASE}/issues/",
                    params={'start_after': start_date, 'start_before': end_date}
                )
                total_fetched = len(all_main)
                main_map = {m['key']: m['fields'] for m in all_main}

                all_sub = ambil_semua_halaman(
                    url=f"{MOCK_JIRA_BASE}/sub-issues/",
                    params={
                        'start_after':  start_date,   # start_date >= tgl mulai
                        'start_before': end_date,     # start_date <= tgl akhir
                        'status':     'Completed',  # sistem hanya ambil yang selesai
                    }
                )

                # ── Kumpulkan parent_key yang hilang dari main_map ────
                # Ambil langsung dari DB lokal (jauh lebih cepat dari HTTP request)
                all_parent_keys     = set(sub['fields']['parent']['key'] for sub in all_sub)
                missing_parent_keys = all_parent_keys - set(main_map.keys())

                if missing_parent_keys:
                    from mock_jira.models import JiraMainTicket
                    db_parents = JiraMainTicket.objects.filter(
                        issue_key__in=missing_parent_keys
                    ).values('issue_key', 'package')

                    for parent in db_parents:
                        main_map[parent['issue_key']] = {
                            'package': parent['package'],
                        }

                # ── Siapkan semua objek ───────────────────────────────
                all_objects = []

                for main in all_main:
                    fields = main['fields']
                    all_objects.append(RawTicket(
                        ticket_key         = main['key'],
                        parent_key         = None,
                        platform           = fields.get('package', ''),
                        summary            = f"Main ticket {main['key']}",
                        status             = fields['status']['name'],
                        start_date         = None,
                        due_date           = None,
                        cycle_time         = None,
                        package_name       = fields.get('package', ''),
                        predefined_process = None,
                        sync_log           = sync_log,
                    ))

                for sub in all_sub:
                    sf  = sub['fields']
                    spk = sf['parent']['key']
                    pf  = main_map.get(spk, {})
                    ct  = hitung_cycle_time(sf.get('start_date'), sf.get('due_date'))
                    proc = sf.get('predefined_process', '')
                    all_objects.append(RawTicket(
                        ticket_key         = sub['key'],
                        parent_key         = spk,
                        platform           = pf.get('package', ''),
                        summary            = f"{proc} - {spk}",
                        status             = sf['status']['name'],
                        start_date         = sf.get('start_date') or None,
                        due_date           = sf.get('due_date')   or None,
                        cycle_time         = ct,
                        package_name       = pf.get('package', ''),
                        predefined_process = proc,
                        sync_log           = sync_log,
                    ))

                # ── Bulk upsert: insert baru, update yang sudah ada ───
                update_fields = [
                    'parent_key', 'platform', 'summary', 'status',
                    'start_date', 'due_date', 'cycle_time',
                    'package_name', 'predefined_process', 'sync_log',
                ]
                for i in range(0, len(all_objects), 500):
                    batch = all_objects[i:i+500]
                    RawTicket.objects.bulk_create(
                        batch,
                        update_conflicts = True,
                        unique_fields    = ['ticket_key'],
                        update_fields    = update_fields,
                        batch_size       = 500,
                    )
                total_processed = len(all_objects)


                sync_log.finished_at     = timezone.now()
                sync_log.total_fetched   = total_fetched
                sync_log.total_processed = total_processed
                sync_log.total_errors    = total_errors
                sync_log.status          = 'success'
                sync_log.save()

                messages.success(request,
                    f"✅ Sync successful! {total_processed} tickets processed."
                )

            except Exception as e:
                sync_log.finished_at = timezone.now()
                sync_log.status      = 'failed'
                sync_log.save()
                messages.error(request, f"❌ Sync failed: {str(e)}")

            return redirect('admin_sync')

        # ── Flag ticket ──
        elif action == 'flag':
            ticket_key = request.POST.get('ticket_key')
            comment    = request.POST.get('comment', '')
            try:
                ticket = RawTicket.objects.get(ticket_key=ticket_key)
                ErrorTicket.objects.get_or_create(
                    ticket=ticket,
                    defaults={
                        'error_message': comment or 'Flagged manually',
                        'flagged_by':    request.user,
                        'raw_payload':   {},
                    }
                )
                FlagHistory.objects.create(
                    ticket=ticket, flagged_by=request.user,
                    action='Flag', comment=comment,
                )
                messages.success(request, f"Ticket {ticket_key} has been flagged.")
            except RawTicket.DoesNotExist:
                messages.error(request, "Ticket not found.")
            return redirect('admin_sync')

        # ── Unflag ticket ──
        elif action == 'unflag':
            ticket_key = request.POST.get('ticket_key')
            try:
                ticket = RawTicket.objects.get(ticket_key=ticket_key)
                ErrorTicket.objects.filter(ticket=ticket).delete()
                messages.success(request, f"Flag removed from ticket {ticket_key}.")
            except RawTicket.DoesNotExist:
                messages.error(request, "Ticket not found.")
            return redirect('admin_sync')

        # ── Create Dashboard ──
        elif action == 'create_dashboard':
            # Cek: tidak boleh ada flagged ticket
            flagged_count = ErrorTicket.objects.count()
            if flagged_count > 0:
                messages.error(request,
                    f"❌ Cannot create dashboard: {flagged_count} flagged ticket(s) still exist. "
                    f"Please resolve all flagged tickets in Data Management first."
                )
                return redirect('admin_sync')

            # Tandai sync terakhir sebagai 'published'
            last_sync = SyncLog.objects.filter(status='success').order_by('-started_at').first()
            if not last_sync:
                messages.error(request, "❌ No successful sync data found to publish.")
                return redirect('admin_sync')

            last_sync.status = 'published'
            last_sync.save(update_fields=['status'])
            messages.success(request,
                "✅ Dashboard created successfully! Clean data is now available in Reports."
            )
            return redirect('admin_sync')

    # ── GET: tampilkan halaman ───────────────────────────────────────────────
    today      = date.today()
    start_date = str(today.replace(day=1))
    end_date   = str(today)

    # Records Tersedia: jumlah sub ticket Completed sesuai range tanggal default
    try:
        from mock_jira.models import JiraSubTicket as JiraSubModel
        total = JiraSubModel.objects.filter(
            status='Completed',
            due_date__gte=start_date,
            due_date__lte=end_date,
        ).count()
        error = None
    except Exception as e:
        total = 0
        error = f"Tidak bisa menghitung records: {str(e)}"

    # ── Tabel: hanya data dari sync terakhir ──────────────────────────────
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # Hanya tampilkan data dari sync yang belum dipublish (status='success')
    # Setelah Create Dashboard, status berubah ke 'published' → tabel kosong
    # sampai ada sync baru
    last_sync = SyncLog.objects.filter(status='success').order_by('-started_at').first()
    flagged_count = ErrorTicket.objects.count()

    sync_data      = []
    paginator      = None
    page_obj       = None
    page_range     = []
    start_index    = 0
    end_index      = 0
    total_filtered = 0
    total_all_raw  = RawTicket.objects.filter(
        sync_log=last_sync, parent_key__isnull=False
    ).count() if last_sync else 0

    if last_sync:
        # Filter ketat hanya dari sync_log yang belum dipublish
        # agar data lama (sudah published) tidak ikut tampil
        qs = RawTicket.objects.filter(
            parent_key__isnull=False,
            sync_log=last_sync,
        ).order_by('ticket_key')

        if query:
            qs = qs.filter(ticket_key__icontains=query)

        # ── Deduplikasi: ambil 1 per ticket_key (id terbesar = terbaru) ──
        from django.db.models import Max as MaxId
        latest_ids = (
            RawTicket.objects
            .filter(id__in=qs.values('id'))
            .values('ticket_key')
            .annotate(max_id=MaxId('id'))
            .values_list('max_id', flat=True)
        )
        qs = RawTicket.objects.filter(id__in=latest_ids).order_by('ticket_key')

        flagged_keys   = set(ErrorTicket.objects.values_list('ticket__ticket_key', flat=True))
        total_filtered = qs.count()

        paginator   = Paginator(qs, per_page)
        page_obj    = paginator.get_page(page_number)
        start_index = (page_obj.number - 1) * per_page + 1
        end_index   = min(page_obj.number * per_page, total_filtered)

        for t in page_obj.object_list:
            item = {
                'ticket_key':         t.ticket_key,
                'parent_key':         t.parent_key,
                'platform':           t.platform,
                'predefined_process': t.predefined_process,
                'status':             t.status,
                'start_date':         t.start_date,
                'due_date':           t.due_date,
                'cycle_time':         t.cycle_time,
                'has_ct':             t.cycle_time is not None,
                'is_flagged':         t.ticket_key in flagged_keys,
            }
            sync_data.append(enrich_ticket(item))
        page_range = get_page_range(page_obj.number, paginator.num_pages)

    context = {
        'start_date':     start_date,
        'end_date':       end_date,
        'total':          total,
        'data_range_str': get_data_range_str(),
        'error':          error,
        'last_sync':      last_sync,
        # tabel
        'sync_data':      sync_data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     page_range,
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
        'total_all':      total_all_raw,
        'flagged_count':  flagged_count,
        'clean_count':    total_all_raw - flagged_count,
        'has_unpublished': last_sync is not None,
        'has_published':   SyncLog.objects.filter(status='published').exists(),
    }
    return render(request, 'main/admin/sync.html', context)


def hitung_cycle_time(start_date_str, due_date_str):
    if not start_date_str or not due_date_str:
        return None
    try:
        start = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end   = datetime.strptime(due_date_str,   '%Y-%m-%d').date()
        # +1 hitung hari pertama; nilai negatif = data error (due < start)
        delta = (end - start).days + 1
        return float(delta)
    except Exception:
        return None


# ======================================================
# DATA MANAGEMENT — dengan pagination
# ======================================================
@login_required
@role_required('admin')
def admin_data(request):
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # ── POST: edit atau resolve ──────────────────────────────────────────────
    if request.method == 'POST':
        action     = request.POST.get('action')
        ticket_key = request.POST.get('ticket_key')

        if action == 'resolve':
            try:
                ticket = RawTicket.objects.get(ticket_key=ticket_key)
                # Hapus flag
                ErrorTicket.objects.filter(ticket=ticket).delete()
                # Update sync_log ke sync terakhir supaya muncul di tabel sync
                last_sync = SyncLog.objects.filter(status='success').order_by('-started_at').first()
                if last_sync:
                    ticket.sync_log = last_sync
                    ticket.save(update_fields=['sync_log'])
                messages.success(request, f"Ticket {ticket_key} has been resolved and returned to sync data.")
            except RawTicket.DoesNotExist:
                messages.error(request, "Ticket not found.")
            return redirect('admin_data')

        elif action == 'edit':
            try:
                ticket = RawTicket.objects.get(ticket_key=ticket_key)
                new_start = request.POST.get('start_date') or None
                new_due   = request.POST.get('due_date')   or None
                new_proc  = request.POST.get('predefined_process') or ticket.predefined_process

                ticket.start_date         = new_start
                ticket.due_date           = new_due
                ticket.predefined_process = new_proc
                ticket.cycle_time         = hitung_cycle_time(new_start, new_due)
                ticket.save()

                messages.success(request, f"Ticket {ticket_key} has been updated.")
            except RawTicket.DoesNotExist:
                messages.error(request, "Ticket not found.")
            return redirect('admin_data')

    # ── GET: tampilkan flagged tickets ───────────────────────────────────────
    flagged_ids = ErrorTicket.objects.values_list('ticket_id', flat=True)
    qs = RawTicket.objects.filter(id__in=flagged_ids).order_by('-id')

    if query:
        qs = qs.filter(ticket_key__icontains=query)

    # Error message & flagged_by per ticket
    error_qs = ErrorTicket.objects.select_related('ticket', 'flagged_by').all()
    error_map      = {e.ticket_id: e.error_message                           for e in error_qs}
    flagged_by_map = {e.ticket_id: e.flagged_by.username if e.flagged_by else '—' for e in error_qs}

    total_all_raw  = RawTicket.objects.filter(parent_key__isnull=False).count()
    total_flagged  = ErrorTicket.objects.count()
    total_ok       = total_all_raw - total_flagged
    total_filtered = qs.count()

    paginator   = Paginator(qs, per_page)
    page_obj    = paginator.get_page(page_number)
    start_index = (page_obj.number - 1) * per_page + 1
    end_index   = min(page_obj.number * per_page, total_filtered)

    data = []
    for t in page_obj.object_list:
        item = {
            'ticket_key':         t.ticket_key,
            'platform':           t.platform,
            'predefined_process': t.predefined_process,
            'status':             t.status,
            'start_date':         t.start_date,
            'due_date':           t.due_date,
            'cycle_time':         t.cycle_time,
            'error_message':      error_map.get(t.id, ''),
            'flagged_by':         flagged_by_map.get(t.id, '—'),
        }
        data.append(enrich_ticket(item))

    context = {
        'data':           data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     get_page_range(page_obj.number, paginator.num_pages),
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
        'total_all':      total_all_raw,
        'total_flagged':  total_flagged,
        'total_ok':       total_ok,
    }
    return render(request, 'main/admin/data.html', context)

def get_page_range(current, total_pages, window=2):
    """
    Buat range nomor halaman dengan ellipsis.
    Contoh: [1, '...', 4, 5, 6, '...', 16]
    """
    pages = []

    # Selalu tampilkan halaman pertama
    pages.append(1)

    start = max(2, current - window)
    end   = min(total_pages - 1, current + window)

    if start > 2:
        pages.append('...')

    for p in range(start, end + 1):
        pages.append(p)

    if end < total_pages - 1:
        pages.append('...')

    # Selalu tampilkan halaman terakhir
    if total_pages > 1:
        pages.append(total_pages)

    return pages


# ======================================================
# USER MANAGEMENT
# ======================================================
@login_required
@role_required('admin')
def admin_users(request):
    users = User.objects.all().order_by('username')
    context = {
        'manage_user':      users,
        'total_admin':      users.filter(role='admin').count(),
        'total_staff':      users.filter(role='staff').count(),
        'total_management': users.filter(role='management').count(),
    }
    return render(request, 'main/admin/users.html', context)


@login_required
@role_required('admin')
def add_user(request):
    if request.method == 'POST':
        User.objects.create(
            username=request.POST.get('username'),
            email=request.POST.get('email'),
            role=request.POST.get('role'),
            password=make_password(request.POST.get('password'))
        )
        messages.success(request, "User berhasil ditambahkan")
    return redirect('admin_users')


@login_required
@role_required('admin')
def edit_user(request, user_id):
    user = User.objects.get(id=user_id)
    if request.method == 'POST':
        user.username = request.POST.get('username')
        user.email    = request.POST.get('email')
        user.role     = request.POST.get('role')
        password      = request.POST.get('password')
        if password:
            user.password = make_password(password)
        user.save()
        messages.success(request, "User berhasil diupdate")
    return redirect('admin_users')


@login_required
@role_required('admin')
def delete_user(request, user_id):
    User.objects.get(id=user_id).delete()
    messages.success(request, "User berhasil dihapus")
    return redirect('admin_users')


# ======================================================
# REPORTS ADMIN
# ======================================================
@login_required
@role_required('admin')
def admin_reports(request):
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # Hanya tampilkan data dari sync yang sudah dipublish (status='published')
    published_syncs = SyncLog.objects.filter(status='published').values_list('id', flat=True)
    qs = RawTicket.objects.filter(
        sync_log__in=published_syncs
    ).order_by('ticket_key')

    if query:
        qs = qs.filter(
            Q(ticket_key__icontains=query) |
            Q(platform__icontains=query)   |
            Q(predefined_process__icontains=query)
        )

    # ── Deduplikasi berdasarkan ticket_key ────────────────────────────────
    seen       = set()
    unique_ids = []
    for t in qs.values('id', 'ticket_key'):
        if t['ticket_key'] not in seen:
            seen.add(t['ticket_key'])
            unique_ids.append(t['id'])
    qs = RawTicket.objects.filter(id__in=unique_ids).order_by('ticket_key')

    # Summary stats: pisah main vs sub ticket
    total_main     = RawTicket.objects.filter(
        sync_log__in=published_syncs, parent_key__isnull=True
    ).count()
    total_sub      = RawTicket.objects.filter(
        sync_log__in=published_syncs, parent_key__isnull=False
    ).count()
    total_filtered = qs.count()

    paginator   = Paginator(qs, per_page)
    page_obj    = paginator.get_page(page_number)
    start_index = (page_obj.number - 1) * per_page + 1
    end_index   = min(page_obj.number * per_page, total_filtered)

    data = []
    for t in page_obj.object_list:
        item = {
            'ticket_key':         t.ticket_key,
            'parent_key':         t.parent_key,
            'platform':           t.platform,
            'predefined_process': t.predefined_process,
            'status':             t.status,
            'start_date':         t.start_date,
            'due_date':           t.due_date,
            'cycle_time':         t.cycle_time,
            'has_ct':             t.cycle_time is not None,
        }
        data.append(enrich_ticket(item))

    context = {
        'data':           data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     get_page_range(page_obj.number, paginator.num_pages),
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
        'total_main':     total_main,
        'total_sub':      total_sub,
    }
    return render(request, 'main/admin/reports.html', context)


@login_required
@role_required('admin')
def download_report(request):
    response = HttpResponse("PDF Report Placeholder", content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="report.pdf"'
    return response


# ======================================================
# DASHBOARD STAFF
# ======================================================
@login_required
@role_required('staff')
def dashboard_staff(request):
    total_ticket  = RawTicket.objects.filter(parent_key__isnull=False).count()
    avg_ct        = RawTicket.objects.filter(
                        cycle_time__isnull=False
                    ).aggregate(avg=Avg('cycle_time'))['avg']
    avg_ct        = round(avg_ct, 1) if avg_ct else 0
    total_flagged = ErrorTicket.objects.count()

    context = {
        'username':      request.user.username,
        'role':          request.user.get_role_display(),
        'total_ticket':  total_ticket,
        'avg_ct':        avg_ct,
        'total_flagged': total_flagged,
        **get_dashboard_filter_options(),
    }
    return render(request, 'main/staff/dashboard_staff.html', context)


# ======================================================
# VIEW DATA STAFF
# ======================================================
@login_required
@role_required('staff')
def staff_view_data(request):
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # ── POST: flag ticket (masuk ke Data Management admin) ──────────────────
    if request.method == 'POST':
        action     = request.POST.get('action')
        ticket_key = request.POST.get('ticket_key')
        comment    = request.POST.get('comment', '')

        if action == 'flag':
            try:
                ticket = RawTicket.objects.get(ticket_key=ticket_key)
                ErrorTicket.objects.get_or_create(
                    ticket=ticket,
                    defaults={
                        'error_message': comment or 'Flagged by staff',
                        'flagged_by':    request.user,
                        'raw_payload':   {},
                    }
                )
                FlagHistory.objects.create(
                    ticket=ticket, flagged_by=request.user,
                    action='Flag', comment=comment,
                )
                messages.success(request, f"Ticket {ticket_key} has been flagged.")
            except RawTicket.DoesNotExist:
                messages.error(request, "Ticket not found.")
        return redirect('staff_view_data')

    # ── GET: tampilkan data dari sync terakhir ───────────────────────────────
    last_sync = SyncLog.objects.filter(status='success').order_by('-started_at').first()

    sync_data      = []
    paginator      = None
    page_obj       = None
    page_range     = []
    start_index    = 0
    end_index      = 0
    total_filtered = 0
    total_all_raw  = RawTicket.objects.filter(parent_key__isnull=False).count()

    if last_sync:
        sync_date_range = RawTicket.objects.filter(
            sync_log=last_sync, start_date__isnull=False
        ).aggregate(earliest=Min('start_date'), latest=Max('start_date'))

        earliest_date = sync_date_range['earliest']
        latest_date   = sync_date_range['latest']

        if earliest_date and latest_date:
            qs = RawTicket.objects.filter(
                parent_key__isnull=False,
                start_date__gte=earliest_date,
                start_date__lte=latest_date,
            ).order_by('ticket_key')
        else:
            qs = RawTicket.objects.filter(
                parent_key__isnull=False,
                sync_log=last_sync,
            ).order_by('ticket_key')

        if query:
            qs = qs.filter(
                Q(ticket_key__icontains=query) |
                Q(predefined_process__icontains=query)
            )

        from django.db.models import Max as MaxId
        latest_ids = (
            RawTicket.objects
            .filter(id__in=qs.values('id'))
            .values('ticket_key')
            .annotate(max_id=MaxId('id'))
            .values_list('max_id', flat=True)
        )
        qs = RawTicket.objects.filter(id__in=latest_ids).order_by('ticket_key')

        flagged_keys   = set(ErrorTicket.objects.values_list('ticket__ticket_key', flat=True))
        total_filtered = qs.count()

        paginator   = Paginator(qs, per_page)
        page_obj    = paginator.get_page(page_number)
        start_index = (page_obj.number - 1) * per_page + 1
        end_index   = min(page_obj.number * per_page, total_filtered)

        for t in page_obj.object_list:
            item = {
                'ticket_key':         t.ticket_key,
                'parent_key':         t.parent_key,
                'platform':           t.platform,
                'predefined_process': t.predefined_process,
                'status':             t.status,
                'start_date':         t.start_date,
                'due_date':           t.due_date,
                'cycle_time':         t.cycle_time,
                'has_ct':             t.cycle_time is not None,
                'is_flagged':         t.ticket_key in flagged_keys,
            }
            sync_data.append(enrich_ticket(item))
        page_range = get_page_range(page_obj.number, paginator.num_pages)

    context = {
        'sync_data':      sync_data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     page_range,
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
        'total_all':      total_all_raw,
        'last_sync':      last_sync,
    }
    return render(request, 'main/staff/view_data.html', context)


# ======================================================
# REPORTS STAFF
# ======================================================
@login_required
@role_required('staff')
def staff_reports(request):
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # Semua clean ticket (tidak diflag)
    flagged_ids = ErrorTicket.objects.values_list('ticket_id', flat=True)
    qs = RawTicket.objects.filter(
        parent_key__isnull=False
    ).exclude(id__in=flagged_ids).order_by('ticket_key')

    if query:
        qs = qs.filter(
            Q(ticket_key__icontains=query) |
            Q(platform__icontains=query)   |
            Q(predefined_process__icontains=query)
        )

    # Deduplikasi
    from django.db.models import Max as MaxId
    latest_ids = (
        RawTicket.objects
        .filter(id__in=qs.values('id'))
        .values('ticket_key')
        .annotate(max_id=MaxId('id'))
        .values_list('max_id', flat=True)
    )
    qs = RawTicket.objects.filter(id__in=latest_ids).order_by('ticket_key')

    total_main     = RawTicket.objects.filter(parent_key__isnull=True).count()
    total_sub      = RawTicket.objects.filter(parent_key__isnull=False).count()
    total_combined = total_main + total_sub
    total_flagged  = flagged_ids.count()
    total_clean    = qs.count()
    total_filtered = qs.count()

    paginator   = Paginator(qs, per_page)
    page_obj    = paginator.get_page(page_number)
    start_index = (page_obj.number - 1) * per_page + 1
    end_index   = min(page_obj.number * per_page, total_filtered)

    data = []
    for t in page_obj.object_list:
        item = {
            'ticket_key':         t.ticket_key,
            'parent_key':         t.parent_key,
            'platform':           t.platform,
            'predefined_process': t.predefined_process,
            'status':             t.status,
            'start_date':         t.start_date,
            'due_date':           t.due_date,
            'cycle_time':         t.cycle_time,
            'has_ct':             t.cycle_time is not None,
        }
        data.append(enrich_ticket(item))

    context = {
        'data':           data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     get_page_range(page_obj.number, paginator.num_pages),
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
        'total_combined': total_combined,
        'total_flagged':  total_flagged,
        'total_clean':    total_clean,
    }
    return render(request, 'main/staff/reports.html', context)


# ======================================================
# DASHBOARD MANAGEMENT
# ======================================================
@login_required
@role_required('management')
def dashboard_management(request):
    total_ticket = RawTicket.objects.filter(parent_key__isnull=False).count()
    avg_ct       = RawTicket.objects.filter(
                       cycle_time__isnull=False
                   ).aggregate(avg=Avg('cycle_time'))['avg']
    avg_ct       = round(avg_ct, 1) if avg_ct else 0

    context = {
        'username':     request.user.username,
        'role':         request.user.get_role_display(),
        'total_ticket': total_ticket,
        'avg_ct':       avg_ct,
        **get_dashboard_filter_options(),
    }
    return render(request, 'main/management/dashboard_management.html', context)


# ======================================================
# REPORTS MANAGEMENT
# ======================================================
@login_required
@role_required('management')
def management_reports(request):
    query       = request.GET.get('q', '')
    page_number = request.GET.get('page', 1)
    per_page    = int(request.GET.get('per_page', 10))
    if per_page not in [10, 25, 50, 100]:
        per_page = 10

    # Semua clean ticket (tidak diflag)
    flagged_ids = ErrorTicket.objects.values_list('ticket_id', flat=True)
    qs = RawTicket.objects.filter(
        parent_key__isnull=False
    ).exclude(id__in=flagged_ids).order_by('ticket_key')

    if query:
        qs = qs.filter(
            Q(ticket_key__icontains=query) |
            Q(platform__icontains=query)   |
            Q(predefined_process__icontains=query)
        )

    # Deduplikasi
    from django.db.models import Max as MaxId
    latest_ids = (
        RawTicket.objects
        .filter(id__in=qs.values('id'))
        .values('ticket_key')
        .annotate(max_id=MaxId('id'))
        .values_list('max_id', flat=True)
    )
    qs = RawTicket.objects.filter(id__in=latest_ids).order_by('ticket_key')

    total_filtered = qs.count()
    paginator   = Paginator(qs, per_page)
    page_obj    = paginator.get_page(page_number)
    start_index = (page_obj.number - 1) * per_page + 1
    end_index   = min(page_obj.number * per_page, total_filtered)

    data = []
    for t in page_obj.object_list:
        item = {
            'ticket_key':         t.ticket_key,
            'parent_key':         t.parent_key,
            'platform':           t.platform,
            'predefined_process': t.predefined_process,
            'status':             t.status,
            'start_date':         t.start_date,
            'due_date':           t.due_date,
            'cycle_time':         t.cycle_time,
            'has_ct':             t.cycle_time is not None,
        }
        data.append(enrich_ticket(item))

    context = {
        'data':           data,
        'query':          query,
        'per_page':       per_page,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     get_page_range(page_obj.number, paginator.num_pages),
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
    }
    return render(request, 'main/management/reports.html', context)


# ======================================================
# CHART API — Data untuk grafik dashboard
# ======================================================
from django.db.models import Avg, Count, F
import json as json_module

@login_required
def chart_data_api(request):
    """
    API endpoint JSON untuk data grafik.
    Diakses oleh semua role via AJAX.
    """
    from main.process_groups import get_process_info
    
    published = get_published_syncs()

    # Hanya sub ticket yang punya cycle_time
    qs = RawTicket.objects.filter(
        parent_key__isnull=False,
        cycle_time__isnull=False,
        platform__isnull=False,
        sync_log__in=published,
    ).exclude(platform='')

    # ── 1. Rata-rata CT per Platform ─────────────────────────
    ct_per_platform = (
        qs.values('platform')
          .annotate(avg_ct=Avg('cycle_time'), count=Count('id'))
          .order_by('platform')
    )

    chart1 = {
        'labels': [],
        'data':   [],
        'counts': [],
    }
    for row in ct_per_platform:
        chart1['labels'].append(row['platform'])
        chart1['data'].append(round(row['avg_ct'], 2))
        chart1['counts'].append(row['count'])

    # ── 2. Rata-rata CT per Platform per Tahun ───────────────
    from django.db.models.functions import ExtractYear
    ct_per_platform_year = (
        qs.annotate(year=ExtractYear('start_date'))
          .filter(year__isnull=False)
          .values('platform', 'year')
          .annotate(avg_ct=Avg('cycle_time'))
          .order_by('platform', 'year')
    )

    # Struktur: { platform: { year: avg_ct } }
    platform_year_map = {}
    years_set = set()
    for row in ct_per_platform_year:
        p = row['platform']
        y = str(row['year'])
        years_set.add(y)
        if p not in platform_year_map:
            platform_year_map[p] = {}
        platform_year_map[p][y] = round(row['avg_ct'], 2)

    years_sorted = sorted(years_set)
    platforms    = sorted(platform_year_map.keys())

    COLORS = [
        '#2563eb','#16a34a','#dc2626','#d97706',
        '#7c3aed','#0891b2','#be185d','#059669',
        '#ea580c','#4338ca','#0d9488','#b45309',
        '#6d28d9',
    ]

    chart2_datasets = []
    for i, platform in enumerate(platforms):
        data_points = []
        for y in years_sorted:
            data_points.append(platform_year_map[platform].get(y, None))
        chart2_datasets.append({
            'label':           platform,
            'data':            data_points,
            'borderColor':     COLORS[i % len(COLORS)],
            'backgroundColor': COLORS[i % len(COLORS)] + '33',
            'tension':         0.3,
            'fill':            False,
        })

    chart2 = {
        'labels':   years_sorted,
        'datasets': chart2_datasets,
    }

    # ── 3. Rata-rata CT per Process Area per Platform ────────
    ct_per_process_platform = (
        qs.filter(predefined_process__isnull=False)
          .exclude(predefined_process='')
          .values('platform', 'predefined_process')
          .annotate(avg_ct=Avg('cycle_time'))
          .order_by('platform', 'predefined_process')
    )

    # Struktur: { platform: { process: avg_ct } }
    process_platform_map = {}
    process_set = set()
    for row in ct_per_process_platform:
        p   = row['platform']
        pr  = row['predefined_process']
        process_set.add(pr)
        if p not in process_platform_map:
            process_platform_map[p] = {}
        process_platform_map[p][pr] = round(row['avg_ct'], 2)

    chart3 = {
        'platforms':  sorted(process_platform_map.keys()),
        'data':       process_platform_map,
    }

    # ── 4. Data untuk estimasi CT (semua kombinasi process+platform) ──
    estimasi_data = {}
    for row in ct_per_process_platform:
        p  = row['platform']
        pr = row['predefined_process']
        if p not in estimasi_data:
            estimasi_data[p] = {}
        estimasi_data[p][pr] = round(row['avg_ct'], 2)

    # Daftar semua process unik
    all_processes = sorted(list(
        RawTicket.objects.filter(
            predefined_process__isnull=False
        ).exclude(predefined_process='')
         .values_list('predefined_process', flat=True)
         .distinct()
    ))

    # Daftar semua platform unik
    all_platforms = sorted(list(
        RawTicket.objects.filter(
            platform__isnull=False
        ).exclude(platform='')
         .values_list('platform', flat=True)
         .distinct()
    ))

    chart4 = {
        'estimasi_data': estimasi_data,
        'all_processes': all_processes,
        'all_platforms': all_platforms,
    }

    return JsonResponse({
        'chart1': chart1,
        'chart2': chart2,
        'chart3': chart3,
        'chart4': chart4,
    })


# ======================================================
# CYCLE TIME ANALYSIS DASHBOARD
# ======================================================
@login_required
def ct_analysis_dashboard(request):
    """Halaman analisis cycle time dengan pivot table dan grafik."""
    from main.process_groups import PROCESS_GROUP, PACKAGE_PLATFORM
    
    published = get_published_syncs()

    # Ambil semua nilai unik untuk filter
    months_qs = RawTicket.objects.filter(
        parent_key__isnull=False,
        start_date__isnull=False,
        cycle_time__isnull=False,
        sync_log__in=published,
    ).dates('start_date', 'month')

    all_months       = sorted(set(f"{d.year}-{str(d.month).zfill(2)}" for d in months_qs))
    all_platforms    = sorted(set(PACKAGE_PLATFORM.values()))
    all_stages       = sorted(set(v[0] for v in PROCESS_GROUP.values()))
    all_areas        = sorted(set(v[1] for v in PROCESS_GROUP.values()))
    all_processes    = sorted(set(PROCESS_GROUP.keys()))

    context = {
        'all_months':   all_months,
        'all_platforms': all_platforms,
        'all_stages':   all_stages,
        'all_areas':    all_areas,
        'all_processes': all_processes,
    }
    return render(request, 'main/partials/ct_analysis.html', context)


@login_required
def ct_analysis_data(request):
    from main.process_groups import PROCESS_GROUP, PACKAGE_PLATFORM
    from collections import defaultdict
    
    published = get_published_syncs()

    f_year      = request.GET.get('year', '').strip()
    f_platforms = request.GET.getlist('platform')
    f_stages    = request.GET.getlist('stage')
    f_areas     = request.GET.getlist('area')
    f_processes = request.GET.getlist('process')

    qs = RawTicket.objects.filter(
        parent_key__isnull=False, 
        cycle_time__isnull=False, 
        start_date__isnull=False,
        sync_log__in=published,
    ).exclude(predefined_process__isnull=True).exclude(predefined_process='') \
     .exclude(platform__isnull=True).exclude(platform='')

    force_monthly = False
    if f_year:
        try:
            qs = qs.filter(start_date__year=int(f_year))
            force_monthly = True
        except ValueError:
            pass
    if f_processes:
        qs = qs.filter(predefined_process__in=f_processes)

    rows = qs.values('platform', 'predefined_process', 'cycle_time', 'start_date')
    enriched = []
    for r in rows:
        proc        = r['predefined_process']
        plat        = r['platform']
        stage, area = PROCESS_GROUP.get(proc, ('Other', 'Other'))
        pg          = PACKAGE_PLATFORM.get(plat, 'Other')
        if f_platforms and pg    not in f_platforms: continue
        if f_stages    and stage not in f_stages:    continue
        if f_areas     and area  not in f_areas:     continue
        sd = r['start_date']
        enriched.append({
            'pg': pg, 'proc': proc, 'stage': stage, 'area': area,
            'ct': float(r['cycle_time']),
            'year': str(sd.year),
            'month': f"{sd.year}-{str(sd.month).zfill(2)}",
        })

    all_years_db = sorted(set(
        str(d.year) for d in RawTicket.objects.filter(
            parent_key__isnull=False, start_date__isnull=False,
        ).dates('start_date', 'year')
    ))

    if not enriched:
        return JsonResponse({
            'col_keys': [], 'col_mode': {}, 'months': [],
            'platform_groups': [], 'all_years': all_years_db,
            'all_areas': [], 'all_processes': [],
            'pivot1': {}, 'pivot2': {},
            'chart': {'labels': [], 'datasets': []},
        })

    if force_monthly:
        # Pilih 1 tahun → tampilkan per bulan
        col_keys = sorted(set(r['month'] for r in enriched))
        col_mode = {ck: 'monthly' for ck in col_keys}
    else:
        # All Years → selalu tampilkan per tahun
        col_keys = sorted(set(r['year'] for r in enriched))
        col_mode = {ck: 'yearly' for ck in col_keys}

    def gcol(r):
        return r['year'] if col_mode.get(r['year']) == 'yearly' else r['month']

    p1_raw = defaultdict(lambda: defaultdict(list))
    for r in enriched:
        p1_raw[(r['pg'], r['stage'], r['area'], r['proc'])][gcol(r)].append(r['ct'])

    pivot1 = {}
    for (pg, stage, area, proc), col_data in p1_raw.items():
        pivot1.setdefault(pg, {}).setdefault(stage, {}).setdefault(area, {})
        apc, av = {}, []
        for ck in col_keys:
            vals = col_data.get(ck, [])
            if vals:
                apc[ck] = round(sum(vals)/len(vals), 1)
                av.extend(vals)
        apc['grand_total'] = round(sum(av)/len(av), 1) if av else None
        pivot1[pg][stage][area][proc] = apc

    pgs_in_data = sorted(set(r['pg'] for r in enriched))
    p2_raw = defaultdict(lambda: defaultdict(list))
    for r in enriched:
        p2_raw[(r['area'], r['proc'])][r['pg']].append(r['ct'])

    pivot2 = {}
    for (area, proc), pg_data in p2_raw.items():
        pivot2.setdefault(area, {})
        pga, av = {}, []
        for pg in pgs_in_data:
            vals = pg_data.get(pg, [])
            if vals:
                pga[pg] = round(sum(vals)/len(vals), 1); av.extend(vals)
        pga['grand_total'] = round(sum(av)/len(av), 1) if av else None
        pivot2[area][proc] = pga

    AREA_COLORS = {
        'Die Attach':'#2563eb','Wire Bond':'#16a34a','Molding':'#dc2626',
        'Trim & Form':'#d97706','Marking':'#7c3aed','Plating':'#0891b2',
        'Ball Mount':'#be185d','Quality Control':'#059669','Testing':'#ea580c',
        'Reliability Test':'#4338ca','Packing':'#0d9488','Shipment':'#b45309',
        'Wafer Preparation':'#6d28d9','Other':'#9ca3af',
    }
    chart_raw = defaultdict(lambda: defaultdict(list))
    for r in enriched:
        chart_raw[r['area']][gcol(r)].append(r['ct'])

    datasets = []
    for area in sorted(chart_raw.keys()):
        pts = [round(sum(chart_raw[area].get(ck,[]))/max(len(chart_raw[area].get(ck,[])),1),1)
               if chart_raw[area].get(ck) else 0 for ck in col_keys]
        nz = [x for x in pts if x]
        pts.append(round(sum(nz)/len(nz),1) if nz else 0)
        datasets.append({
            'label': area, 'data': pts,
            'backgroundColor': AREA_COLORS.get(area,'#9ca3af')+'cc',
            'borderColor': AREA_COLORS.get(area,'#9ca3af'), 'borderWidth': 1,
        })

    return JsonResponse({
        'col_keys': col_keys, 'col_mode': col_mode, 'months': col_keys,
        'use_yearly': not force_monthly,
        'platform_groups': pgs_in_data, 'all_years': all_years_db,
        'all_areas': sorted(pivot2.keys()),
        'all_processes': sorted(set(p for ad in pivot2.values() for p in ad.keys())),
        'pivot1': pivot1, 'pivot2': pivot2,
        'chart': {'labels': col_keys + ['Grand Total'], 'datasets': datasets},
    })