# ============================================================
# main/services.py
# ============================================================
import matplotlib
matplotlib.use('Agg')

import io
import logging
from collections import defaultdict
from datetime    import datetime

import matplotlib.patches as mpatches
import matplotlib.pyplot  as plt
import numpy              as np
import requests

from django.conf           import settings
from django.core.paginator import Paginator
from django.db.models      import Q
from django.utils          import timezone

# ReportLab = library untuk generate file PDF
from reportlab.lib            import colors
from reportlab.lib.pagesizes  import A4, landscape
from reportlab.lib.styles     import ParagraphStyle
from reportlab.lib.units      import cm
from reportlab.platypus       import (
    Image as RLImage, PageBreak, Paragraph,
    SimpleDocTemplate, Table, TableStyle,
)

from main.models        import RawTicket, SyncLog
from main.process_groups import PACKAGE_PLATFORM, PROCESS_GROUP, enrich_ticket, get_platform
from main.utils         import (
    get_page_range,
    hitung_cycle_time,
    pdf_format_column_label,
    pdf_value_to_string,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
# Konfigurasi koneksi ke Mock JIRA Server (http://127.0.0.1:8001/mock-jira/api/)
_BASE_URL       = settings.MOCK_JIRA_BASE_URL
_TIMEOUT        = settings.MOCK_JIRA_TIMEOUT
_PAGE_SIZE      = settings.MOCK_JIRA_PAGE_SIZE
_URL_MAIN       = f"{_BASE_URL}/issues/"                # untuk ambil MAIN ticket
_URL_SUB        = f"{_BASE_URL}/sub-issues/"            # untuk ambil SUB ticket
_URL_SUB_RANGE  = f"{_BASE_URL}/sub-issues/range/"      # untuk cek rentang tanggal data yang tersedia di JIRA
_META_KEYS      = frozenset({'total', 'page', 'page_size', 'total_pages'})

API_BATCH_SIZE  = 50    # jumlah ticket per batch request ke Mock JIRA API
DB_BATCH_SIZE   = 500   # jumlah row per batch saat bulk insert ke database

# ── PDF visual constants ───────────────────────────────────────────────────────
_PG_COLORS = {
    'BGA': '#2563eb', 'SOT': '#9ca3af', 'QFN': '#d97706',
    'QFP': '#111827', 'SOP': '#dc2626', 'TO':  '#374151',
    'Other': '#6b7280',
}
_FALLBACK_COLORS = [ 
    '#2563eb', '#16a34a', '#dc2626', '#d97706',
    '#7c3aed', '#0891b2', '#be185d', '#059669',
]
_TABLE_COLORS = {
    'header':   colors.HexColor('#1e293b'),
    'gt_bg':    colors.HexColor('#bfdbfe'),
    'subtot':   colors.HexColor('#e0f2fe'),
    'even_row': colors.HexColor('#f8fafc'),
    'border':   colors.HexColor('#e5e7eb'),
    'gt_val':   colors.HexColor('#1e40af'),
    'sub_val':  colors.HexColor('#0369a1'),
}


# ======================================================
# API: PAGINATED FETCH
# ======================================================

def ambil_semua_halaman(url, params, timeout=None):
    hasil    = []
    page     = 1
    key_data = None
    timeout  = timeout or _TIMEOUT

    while True:
        params['page']      = page
        params['page_size'] = _PAGE_SIZE

        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if key_data is None:
            key_data = next((k for k in data if k not in _META_KEYS), None)

        hasil      += data.get(key_data, [])
        total_pages = data.get('total_pages', 1)

        if page >= total_pages:
            break
        page += 1

    return hasil


def ambil_main_tickets_by_keys(issue_keys):
    """Fetch only required main tickets for given parent keys in batches."""
    hasil = []
    keys  = list(issue_keys)

    for i in range(0, len(keys), API_BATCH_SIZE):
        hasil.extend(ambil_semua_halaman(
            url=_URL_MAIN,
            params={'issue_key': keys[i:i + API_BATCH_SIZE]},
        ))

    return hasil


# ======================================================
# QUERY: HITUNG Jumlah RECORDS TERSEDIA (Records Available)
# ======================================================

def hitung_available_records(start_date, end_date):
    try:
        resp = requests.get(
            _URL_SUB,
            params={
                'due_after':  start_date,
                'due_before': end_date,
                'status':     'Completed',
                'page':       1,
                'page_size':  1,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get('total', 0), None
    except requests.exceptions.ConnectionError:
        return 0, f"Mock Jira API tidak dapat dijangkau ({_BASE_URL})."
    except requests.exceptions.Timeout:
        return 0, "Mock Jira API timeout."
    except Exception as e:
        logger.warning(f"[available_records] HTTP query gagal: {e}")
        return 0, f"Tidak bisa menghitung records: {str(e)}"

def get_jira_due_date_range():
    """
    Cek rentang tanggal (paling awal - paling akhir) dari SELURUH data yang ada di Mock JIRA.
    """
    try:
        resp = requests.get(_URL_SUB_RANGE, timeout=_TIMEOUT) # Kirim request GET ke URL /sub-issues/range/
        resp.raise_for_status()
        data = resp.json()

        earliest_str = data.get('earliest_due_date')
        latest_str   = data.get('latest_due_date')

        if earliest_str and latest_str:
            return (
                datetime.strptime(earliest_str, '%Y-%m-%d').date(),
                datetime.strptime(latest_str,   '%Y-%m-%d').date(),
            )
    except requests.exceptions.ConnectionError:
        # JIRA tidak bisa dijangkau (mati/offline)
        logger.warning("[get_jira_due_date_range] MockJira tidak bisa dijangkau.")
    except requests.exceptions.Timeout:
        # JIRA terlalu lama merespon (> 30 detik)
        logger.warning("[get_jira_due_date_range] Request ke MockJira timeout.")
    except Exception as e:
        logger.warning(f"[get_jira_due_date_range] Error: {e}")

    return None # kalau gagal/error, return None (bukan raise) supaya halaman tetap tampil

# ======================================================
# SERVICE: SYNC JIRA DATA - Transform
# ======================================================

class SyncResult:
    """
    Attributes:
        success              : True jika sync selesai tanpa exception fatal
        total_fetched        : jumlah sub-ticket yang diambil dari API
        total_processed      : jumlah ticket BARU yang berhasil di-insert ke DB (main + sub)
        total_processed_main : jumlah main ticket BARU yang di-insert
        total_processed_sub  : jumlah sub-ticket BARU yang di-insert
        total_skipped        : jumlah ticket yang di-skip karena sudah ada di DB
        total_skipped_sub    : jumlah sub-ticket yang di-skip (sudah ada di DB)
        error_type           : 'connection' | 'timeout' | 'empty' | 'unknown' | None
        error_detail         : pesan error mentah
    """
    def __init__(self):
        self.success              = False
        self.total_fetched        = 0
        self.total_processed      = 0
        self.total_processed_main = 0
        self.total_processed_sub  = 0
        self.total_skipped        = 0
        self.total_skipped_sub    = 0
        self.error_type           = None
        self.error_detail         = None


def _build_main_objects(main_map, sync_log):
    """Bangun list RawTicket untuk main tickets ke database."""
    objects = []
    for key, fields in main_map.items():
        status_raw = fields.get('status', '')
        objects.append(RawTicket(
            ticket_key         = key,
            parent_key         = None,
            platform           = get_platform(fields.get('package', '')),
            summary            = f"Main ticket {key}",
            status             = status_raw.get('name', '') if isinstance(status_raw, dict) else status_raw,
            start_date         = None,
            due_date           = None,
            cycle_time         = None,
            quantity           = fields.get('quantity') or None,
            package_name       = fields.get('package', ''),
            predefined_process = None,
            sync_log           = sync_log,
        ))
    return objects


def _build_sub_objects(all_sub, main_map, sync_log):
    """Bangun list RawTicket untuk sub-tickets ke database."""
    objects = []
    for sub in all_sub:
        sf         = sub['fields']
        parent_key = sf.get('parent', {}).get('key', '')
        pf         = main_map.get(parent_key, {})
        package    = pf.get('package', '')
        proc       = sf.get('predefined_process', '') or ''
        status_raw = sf.get('status', '')
        enriched   = enrich_ticket({'platform': package, 'predefined_process': proc})

        objects.append(RawTicket(
            ticket_key         = sub['key'],
            parent_key         = parent_key,
            platform           = enriched['platform_group'],
            summary            = f"{proc} - {parent_key}" if proc and parent_key else sub['key'],
            status             = status_raw.get('name', '') if isinstance(status_raw, dict) else status_raw,
            start_date         = sf.get('start_date') or None,
            due_date           = sf.get('due_date') or None,
            cycle_time         = hitung_cycle_time(sf.get('start_date'), sf.get('due_date')),
            quantity           = pf.get('quantity') or None,  # CT per unit dihitung di views.py
            package_name       = package,
            predefined_process = proc,
            sync_log           = sync_log,
        ))
    return objects


def sync_jira_data(user, start_date, end_date):
    """
    SKIP-IF-EXISTS:
    Ticket yang sudah ada di DB (berdasarkan ticket_key) tidak akan
    di-insert ulang dan tidak akan muncul di tabel sync.
    Hanya ticket BARU yang masuk ke DB dan ditampilkan di UI.
    """
    result   = SyncResult()
    sync_log = SyncLog.objects.create(
        admin=user, started_at=timezone.now(), status='running'
    )

    try:
        # ── Step 1: Fetch sub-tickets  dengan due date─────────────────────────────────────
        all_sub = ambil_semua_halaman(
            url=_URL_SUB,
            params={'due_after' : start_date, 
                    'due_before': end_date, 
                    'status'    : 'Completed'},
        )
        sync_log.total_fetched      = result.total_fetched
        logger.info(f"[Sync #{sync_log.id}] Fetched {result.total_fetched} sub-tickets")

        if not all_sub:
            sync_log.finished_at = timezone.now()
            sync_log.status      = 'success'
            sync_log.save()
            result.success    = True
            result.error_type = 'empty'
            return sync_log, result

        # ── Step 2: Fetch parent main tickets ─────────────────────────────
        all_parent_keys = {
            sub['fields']['parent']['key']
            for sub in all_sub
            if sub.get('fields', {}).get('parent')
        }
        main_map = {}
        if all_parent_keys:
            try:
                main_map = {
                    m['key']: m['fields']
                    for m in ambil_main_tickets_by_keys(all_parent_keys) #
                }
                logger.info(
                    f"[Sync #{sync_log.id}] Resolved "
                    f"{len(main_map)}/{len(all_parent_keys)} parent keys via API"
                )
            except requests.exceptions.RequestException as e:
                logger.warning(f"[Sync #{sync_log.id}] Gagal fetch main issues via API: {e}")

        # ── Step 3: Bangun objek RawTicket ────────────────────────────────
        main_objects = _build_main_objects(main_map, sync_log)
        sub_objects  = _build_sub_objects(all_sub, main_map, sync_log)
        all_objects  = main_objects + sub_objects
        result.total_fetched = len(main_objects) + len(sub_objects)  # Main + Sub

        # ── Step 4: Filter hanya ticket baru (duplictae)──────────────────────────────
        existing_keys = set(
            RawTicket.objects
            .filter(ticket_key__in=[o.ticket_key for o in all_objects])
            .values_list('ticket_key', flat=True)
        )
        new_objects = [o for o in all_objects if o.ticket_key not in existing_keys]

        # Hitung processed dan skipped untuk main dan sub ticket
        main_keys_fetched = {o.ticket_key for o in main_objects}
        sub_keys_fetched  = {o.ticket_key for o in sub_objects}
        
        main_keys_skipped = main_keys_fetched & existing_keys
        sub_keys_skipped  = sub_keys_fetched & existing_keys
        
        result.total_skipped     = len(main_keys_skipped) + len(sub_keys_skipped)
        result.total_skipped_sub = len(sub_keys_skipped)
        
        # Pisahkan main dan sub dari new_objects
        main_new = [o for o in new_objects if o.parent_key is None]
        sub_new  = [o for o in new_objects if o.parent_key is not None]
        result.total_processed_main = len(main_new)
        result.total_processed_sub  = len(sub_new)
        
        logger.info(
            f"[Sync #{sync_log.id}] "
            f"{len(new_objects)} baru akan di-insert "
            f"({result.total_processed_main} main + {result.total_processed_sub} sub), "
            f"{result.total_skipped} sudah ada di DB (di-skip)"
        )

        # ── Step 5: Bulk INSERT hanya ticket baru ─────────────────────────
        for i in range(0, len(new_objects), DB_BATCH_SIZE):
            RawTicket.objects.bulk_create(
                new_objects[i:i + DB_BATCH_SIZE],
                ignore_conflicts=True,
                batch_size=DB_BATCH_SIZE,
            )

        result.total_processed = len(new_objects)
        logger.info(
            f"[Sync #{sync_log.id}] Done — "
            f"{result.total_processed} ticket baru disimpan "
            f"({result.total_processed_main} main + {result.total_processed_sub} sub), "
            f"{result.total_skipped} dilewati"
        )

        # ── Step 6: Update SyncLog ────────────────────────────────────────
        # Jika semua di-skip (tidak ada ticket baru), status = 'skipped'
        # agar tidak mengganggu last_sync yang sudah published
        if result.total_processed == 0 and result.total_skipped > 0:
            sync_status = 'skipped'
            result.error_type = 'all_skipped'
        else:
            sync_status = 'success'

        sync_log.finished_at        = timezone.now()
        sync_log.total_fetched      = result.total_fetched
        sync_log.total_processed    = result.total_processed
        sync_log.total_processed_main = result.total_processed_main
        sync_log.total_processed_sub  = result.total_processed_sub
        sync_log.total_skipped      = result.total_skipped
        sync_log.total_skipped_main = len(main_keys_skipped)
        sync_log.total_skipped_sub  = result.total_skipped_sub
        sync_log.total_errors       = 0
        sync_log.status             = sync_status
        sync_log.save()
        result.success = True

    except requests.exceptions.ConnectionError:
        sync_log.finished_at = timezone.now()
        sync_log.status      = 'failed'
        sync_log.save()
        result.error_type   = 'connection'
        result.error_detail = _BASE_URL

    except requests.exceptions.Timeout:
        sync_log.finished_at = timezone.now()
        sync_log.status      = 'failed'
        sync_log.save()
        result.error_type = 'timeout'

    except Exception as e:
        sync_log.finished_at = timezone.now()
        sync_log.status      = 'failed'
        sync_log.save()
        logger.exception(f"[Sync #{sync_log.id}] Unexpected error: {e}")
        result.error_type   = 'unknown'
        result.error_detail = str(e)

    return sync_log, result


# ======================================================
# HELPERS FOR VIEWS
# ======================================================

#mengambil id sync log yg pub;ished
def get_published_syncs():
    return SyncLog.objects.filter(status='published').values_list('id', flat=True)

# filter dashboard
def get_dashboard_filter_options():
    published = get_published_syncs()

    years_qs = RawTicket.objects.filter(
        parent_key__isnull=False,
        start_date__isnull=False,
        cycle_time__isnull=False,
        sync_log__in=published,
    ).dates('start_date', 'year')

    return {
        'all_years':     sorted({str(d.year) for d in years_qs}),
        'all_platforms': sorted(set(PACKAGE_PLATFORM.values())),
        'all_stages':    sorted({pg[0] for pg in PROCESS_GROUP.values()}),
        'all_areas':     sorted({pg[1] for pg in PROCESS_GROUP.values()}),
        'all_processes': sorted(PROCESS_GROUP.keys()),
    }


def _dedupe_latest_ticket_ids(qs):
    seen, unique_ids = set(), []
    for t in qs.values('id', 'ticket_key'):
        if t['ticket_key'] not in seen:
            seen.add(t['ticket_key'])
            unique_ids.append(t['id'])
    return unique_ids

# mengambil data ticket yang clean
def get_published_clean_ticket_queryset(query='', date_from='', date_to=''):
    qs = RawTicket.objects.filter(
        sync_log__in=get_published_syncs(),
        parent_key__isnull=False,
    ).order_by('ticket_key')

    if query:
        qs = qs.filter(
            Q(ticket_key__icontains=query) |
            Q(platform__icontains=query)   |
            Q(predefined_process__icontains=query)
        )
    if date_from:
        qs = qs.filter(start_date__gte=date_from)
    if date_to:
        qs = qs.filter(start_date__lte=date_to)

    return RawTicket.objects.filter(
        id__in=_dedupe_latest_ticket_ids(qs)
    ).order_by('ticket_key')


def paginate_and_enrich_tickets(qs, page_number, per_page):
    paginator      = Paginator(qs, per_page)
    page_obj       = paginator.get_page(page_number)
    total_filtered = qs.count()

    data = [
        enrich_ticket({
            'ticket_key':         t.ticket_key,
            'parent_key':         t.parent_key,
            'platform':           t.platform,
            'package_name':       t.package_name,
            'predefined_process': t.predefined_process,
            'status':             t.status,
            'start_date':         t.start_date,
            'due_date':           t.due_date,
            'cycle_time':         t.cycle_time,
            'has_ct':             t.cycle_time is not None,
        })
        for t in page_obj.object_list
    ]

    start_index = (page_obj.number - 1) * per_page + 1
    end_index   = min(page_obj.number * per_page, total_filtered)

    return {
        'data':           data,
        'page_obj':       page_obj,
        'paginator':      paginator,
        'page_range':     get_page_range(page_obj.number, paginator.num_pages),
        'start_index':    start_index,
        'end_index':      end_index,
        'total_filtered': total_filtered,
    }


# ======================================================
# CT ANALYSIS — private helpers
# ======================================================

def _enrich_ct_rows(qs, f_platforms, f_stages, f_areas, force_monthly):
    """Ambil rows dari DB, filter, dan tambah computed fields."""
    _VALID_GROUPS = set(PACKAGE_PLATFORM.values())
    enriched = []

    for r in qs.values('platform', 'predefined_process', 'cycle_time', 'quantity', 'start_date'):
        proc        = r['predefined_process']
        plat        = r['platform']
        stage, area = PROCESS_GROUP.get(proc, ('Other', 'Other'))
        pg          = plat if plat in _VALID_GROUPS else 'Other'

        if f_platforms and pg    not in f_platforms: continue
        if f_stages    and stage not in f_stages:    continue
        if f_areas     and area  not in f_areas:     continue

        sd = r['start_date']
        enriched.append({
            'pg':     pg,
            'proc':   proc,
            'stage':  stage,
            'area':   area,
            'ct_raw': float(r['cycle_time']),
            'qty':    r['quantity'] or 0,
            'year':   str(sd.year),
            'month':  f"{sd.year}-{str(sd.month).zfill(2)}",
        })

    return enriched

def _build_pivot1(enriched, col_keys, force_monthly):
    """
    Pivot: platform → stage → area → process → {col: avg_ct, grand_total: avg_ct}

    Struktur tambahan yang dikirim ke frontend:
        pivot[pg]['_stage_totals'][stage][col] = SUM avg proses di stage tsb
        pivot[pg]['_stage_totals'][stage]['grand_total'] = SUM grand_total proses
        pivot[pg]['_grand_total'][col] = SUM subtotal stage
        pivot[pg]['_grand_total']['grand_total'] = SUM grand_total stage
    """
    def gcol(r): return r['month'] if force_monthly else r['year']

    raw = defaultdict(lambda: defaultdict(list))
    for r in enriched:
        raw[(r['pg'], r['stage'], r['area'], r['proc'])][gcol(r)].append(r['ct_raw'])
    
    # avg per kolom per process
    pivot = {}
    for (pg, stage, area, proc), col_data in raw.items():
        pivot.setdefault(pg, {}).setdefault(stage, {}).setdefault(area, {})
        apc, all_vals = {}, []
        for ck in col_keys:
            vals = col_data.get(ck, [])
            if vals:
                apc[ck] = round(sum(vals) / len(vals), 1)   # AVG per kolom
                all_vals.extend(vals)
        apc['grand_total'] = round(sum(all_vals) / len(all_vals), 1) if all_vals else None
        pivot[pg][stage][area][proc] = apc

    # ── Hitung subtotal stage dan grand total platform ──────────────────
    for pg in pivot:
        stage_totals  = {}
        pg_col_totals = {ck: 0.0 for ck in col_keys}
        pg_gt_total   = 0.0
        pg_col_has    = {ck: False for ck in col_keys}
        pg_gt_has     = False

        for stage in pivot[pg]:
            if stage.startswith('_'):
                continue
            st_col_totals = {ck: 0.0 for ck in col_keys}
            st_gt_total   = 0.0
            st_col_has    = {ck: False for ck in col_keys}
            st_gt_has     = False

            for area in pivot[pg][stage]:
                for proc in pivot[pg][stage][area]:
                    d = pivot[pg][stage][area][proc]
                    for ck in col_keys:
                        v = d.get(ck)
                        if v is not None:
                            st_col_totals[ck] += v
                            st_col_has[ck]     = True
                    gt = d.get('grand_total')
                    if gt is not None:
                        st_gt_total += gt
                        st_gt_has    = True

            # Simpan subtotal stage
            st_result = {}
            for ck in col_keys:
                st_result[ck] = round(st_col_totals[ck], 1) if st_col_has[ck] else None
            st_result['grand_total'] = round(st_gt_total, 1) if st_gt_has else None
            stage_totals[stage] = st_result

            # Akumulasi ke platform grand total
            for ck in col_keys:
                if st_col_has[ck]:
                    pg_col_totals[ck] += st_col_totals[ck]
                    pg_col_has[ck]     = True
            if st_gt_has:
                pg_gt_total += st_gt_total
                pg_gt_has    = True

        # Simpan grand total platform
        # Hitung count ticket per kolom per platform dari enriched
        pg_count_per_col = {}
        pg_count_total   = 0
        for r in enriched:
            if r['pg'] != pg:
                continue
            col_key = r['month'] if force_monthly else r['year']
            pg_count_per_col[col_key] = pg_count_per_col.get(col_key, 0) + 1
            pg_count_total += 1

        # Simpan grand total platform
        pg_result = {}
        for ck in col_keys:
            pg_result[ck] = round(pg_col_totals[ck], 1) if pg_col_has[ck] else None
        pg_result['grand_total']  = round(pg_gt_total, 1) if pg_gt_has else None
        pg_result['_count']       = pg_count_per_col
        pg_result['_count_total'] = pg_count_total

        pivot[pg]['_stage_totals'] = stage_totals
        pivot[pg]['_grand_total']  = pg_result

    return pivot


def _build_pivot2(enriched, pgs_in_data):
    """Pivot: area → process → {platform: avg_ct, count, grand_total, area_subtotal, grand_total_row}."""
    raw = defaultdict(lambda: defaultdict(list))
    for r in enriched:
        raw[(r['area'], r['proc'])][r['pg']].append(r['ct_raw'])

    pivot = {}
    for (area, proc), pg_data in raw.items():
        pivot.setdefault(area, {})
        pga, all_vals = {}, []
        for pg in pgs_in_data:
            vals = pg_data.get(pg, [])
            if vals:
                pga[pg]             = round(sum(vals) / len(vals), 1)
                pga[f'{pg}__count'] = len(vals)
                all_vals.extend(vals)
        pga['grand_total'] = round(sum(all_vals) / len(all_vals), 1) if all_vals else None
        pga['grand_count'] = len(all_vals)
        pivot[area][proc]  = pga

    # Hitung area subtotal dan grand total row — dikirim ke frontend
    for area in pivot:
        pg_area_vals = defaultdict(list)
        all_area_vals = []
        for proc in pivot[area]:
            if proc.startswith('_'):
                continue
            for pg in pgs_in_data:
                v = pivot[area][proc].get(pg)
                if v is not None:
                    pg_area_vals[pg].append(v)
                    all_area_vals.append(v)
        area_sub = {}
        for pg in pgs_in_data:
            vs = pg_area_vals[pg]
            area_sub[pg] = round(sum(vs) / len(vs), 1) if vs else None
        area_sub['grand_total'] = round(sum(all_area_vals) / len(all_area_vals), 1) if all_area_vals else None
        pivot[area]['_subtotal'] = area_sub

    # Grand total seluruh tabel
    pg_all_vals = defaultdict(list)
    all_grand_vals = []
    for area in pivot:
        for proc in pivot[area]:
            if proc.startswith('_'):
                continue
            for pg in pgs_in_data:
                v = pivot[area][proc].get(pg)
                if v is not None:
                    pg_all_vals[pg].append(v)
                    all_grand_vals.append(v)
    grand_row = {}
    for pg in pgs_in_data:
        vs = pg_all_vals[pg]
        grand_row[pg] = round(sum(vs) / len(vs), 1) if vs else None
    grand_row['grand_total'] = round(sum(all_grand_vals) / len(all_grand_vals), 1) if all_grand_vals else None
    pivot['_grand_total'] = grand_row

    return pivot


def _build_pivot_proposal(enriched):
    raw = defaultdict(lambda: defaultdict(lambda: {'ct_vals': [], 'qty_vals': [], 'area': ''}))
    for r in enriched:
        raw[r['pg']][r['proc']]['ct_vals'].append(r['ct_raw'])
        raw[r['pg']][r['proc']]['area'] = r['area']
        if r['qty'] and r['qty'] > 0:
            raw[r['pg']][r['proc']]['qty_vals'].append(r['qty'])

    pivot = {}
    for pg, procs in raw.items():
        pivot[pg] = {}
        for proc, d in procs.items():
            ct_vals  = d['ct_vals']
            qty_vals = d['qty_vals']

            # Pasangkan qty dan ct — hanya ambil ct yang punya pasangan qty
            pairs = list(zip(qty_vals, ct_vals[:len(qty_vals)]))

            slope, intercept, use_regression, insufficient = None, None, False, False

            if len(pairs) >= 2:
                # Cukup data — hitung regresi linear
                n      = len(pairs)
                xs     = [p[0] for p in pairs]
                ys     = [p[1] for p in pairs]
                x_mean = sum(xs) / n
                y_mean = sum(ys) / n
                num    = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
                den    = sum((x - x_mean) ** 2 for x in xs)
                if den > 0:
                    slope          = round(num / den, 8)
                    intercept      = round(y_mean - slope * x_mean, 4)
                    use_regression = True
                else:
                    # Semua qty sama persis — regresi tidak bisa, insufficient
                    insufficient = True
            else:
                # Kurang dari 2 pasangan data — insufficient
                insufficient = True

            pivot[pg][proc] = {
                'slope':          slope,
                'intercept':      intercept,
                'use_regression': use_regression,
                'insufficient':   insufficient,
                'n':              len(ct_vals),
                'n_pairs':        len(pairs),
                'area':           d['area'],
            }

    return pivot


def _build_ct_chart(enriched, col_keys, force_monthly):
    """Dataset chart per platform — avg CT dari _grand_total pivot1."""
    def gcol(r): return r['month'] if force_monthly else r['year']

    # Kumpulkan data per platform per kolom
    raw_pg = defaultdict(lambda: defaultdict(list))
    raw_pg_count = defaultdict(lambda: defaultdict(int))
    for r in enriched:
        raw_pg[r['pg']][gcol(r)].append(r['ct_raw'])
        raw_pg_count[r['pg']][gcol(r)] += 1

    datasets = []
    pgs = sorted(raw_pg.keys())
    for pg in pgs:
        col_data  = raw_pg[pg]
        pts       = []
        counts    = []
        for ck in col_keys:
            vals = col_data.get(ck, [])
            pts.append(round(sum(vals) / len(vals), 1) if vals else None)
            counts.append(len(vals))
        # Grand avg & grand count
        all_vals = [v for vs in col_data.values() for v in vs]
        pts.append(round(sum(all_vals) / len(all_vals), 1) if all_vals else None)
        counts.append(len(all_vals))

        color = _PG_COLORS.get(pg, _FALLBACK_COLORS[pgs.index(pg) % len(_FALLBACK_COLORS)])
        datasets.append({
            'label':           pg,
            'data':            pts,
            'counts':          counts,
            'backgroundColor': color + 'cc',
            'borderColor':     color,
            'borderWidth':     1,
        })

    return {'labels': col_keys + ['Grand Avg'], 'datasets': datasets}


# ======================================================
# CT ANALYSIS — public entry point
# ======================================================

def compute_ct_analysis(f_year, f_platforms, f_stages, f_areas, f_processes):
    published = get_published_syncs()

    qs = (
        RawTicket.objects
        .filter(
            parent_key__isnull=False,
            cycle_time__isnull=False,
            start_date__isnull=False,
            sync_log__in=published,
        )
        .exclude(predefined_process__isnull=True).exclude(predefined_process='')
        .exclude(platform__isnull=True).exclude(platform='')
    )

    force_monthly = False
    if f_year:
        try:
            qs            = qs.filter(start_date__year=int(f_year))
            force_monthly = True
        except ValueError:
            pass
    if f_processes:
        qs = qs.filter(predefined_process__in=f_processes)

    all_years_db = sorted({
        str(d.year)
        for d in RawTicket.objects.filter(
            parent_key__isnull=False,
            start_date__isnull=False,
        ).dates('start_date', 'year')
    })

    enriched = _enrich_ct_rows(qs, f_platforms, f_stages, f_areas, force_monthly)

    if not enriched:
        return {
            'col_keys': [], 'col_mode': {}, 'months': [],
            'platform_groups': [], 'all_years': all_years_db,
            'all_areas': [], 'all_processes': [],
            'pivot1': {}, 'pivot2': {}, 'pivot_proposal': {},
            'chart': {'labels': [], 'datasets': []},
        }

    col_keys = sorted({r['month'] if force_monthly else r['year'] for r in enriched})
    col_mode = {ck: ('monthly' if force_monthly else 'yearly') for ck in col_keys}

    pgs_in_data    = sorted({r['pg'] for r in enriched})
    pivot1         = _build_pivot1(enriched, col_keys, force_monthly)
    pivot2         = _build_pivot2(enriched, pgs_in_data)
    pivot_proposal = _build_pivot_proposal(enriched)
    chart          = _build_ct_chart(enriched, col_keys, force_monthly)

    return {
        'col_keys':        col_keys,
        'col_mode':        col_mode,
        'months':          col_keys,
        'use_yearly':      not force_monthly,
        'platform_groups': pgs_in_data,
        'all_years':       all_years_db,
        'all_areas':       sorted(pivot2),
        'all_processes':   sorted({p for ad in pivot2.values() for p in ad}),
        'pivot1':          pivot1,
        'pivot2':          pivot2,
        'pivot_proposal':  pivot_proposal,
        'chart':           chart,
    }


# ======================================================
# PDF GENERATION — private helpers
# ======================================================

def _pdf_make_styles():
    mk = lambda name, **kw: ParagraphStyle(name, **kw)
    return {
        'title':    mk('T',  fontSize=16, fontName='Helvetica-Bold',
                        textColor=colors.HexColor('#1e293b'), spaceAfter=4),
        'subtitle': mk('Su', fontSize=8,
                        textColor=colors.HexColor('#64748b'), spaceAfter=8),
        'section':  mk('H',  fontSize=11, fontName='Helvetica-Bold',
                        textColor=colors.HexColor('#1e40af'), spaceBefore=6, spaceAfter=4),
        'note':     mk('N',  fontSize=7.5, textColor=colors.HexColor('#6b7280')),
    }


def _pdf_base_table_styles():
    return [
        ('BACKGROUND', (0, 0), (-1, 0), _TABLE_COLORS['header']),
        ('TEXTCOLOR',  (0, 0), (-1, 0), colors.white),
        ('FONTNAME',   (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0, 0), (-1, -1), 7),
        ('ALIGN',      (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('GRID',       (0, 0), (-1, -1), 0.3, _TABLE_COLORS['border']),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, _TABLE_COLORS['even_row']]),
        ('BACKGROUND', (-1, 0), (-1, 0), colors.HexColor('#1e40af')),
    ]


def _pdf_build_chart(pivot1, platform_groups, col_keys, use_yearly):
    """Render bar chart ke BytesIO PNG buffer."""
    if not platform_groups or not col_keys:
        return None

    pg_col_vals = {pg: {ck: [] for ck in col_keys} for pg in platform_groups}
    pg_all_vals = {pg: [] for pg in platform_groups}
    
    #  ambil langsung dari _grand_total (baris biru pivot 1)
    for pg in platform_groups:
        pg_gt = pivot1.get(pg, {}).get('_grand_total', {})
        for ck in col_keys:
            v = pg_gt.get(ck)
            if v is not None:
                pg_col_vals[pg][ck].append(v)
        gt = pg_gt.get('grand_total')
        if gt is not None:
            pg_all_vals[pg].append(gt)

    x_labels = [pdf_format_column_label(ck, use_yearly) for ck in col_keys] + ['Grand Avg']
    n_groups  = len(x_labels)
    n_pgs     = len(platform_groups)
    bar_width = 0.7 / max(n_pgs, 1)

    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    x_pos          = np.arange(n_groups)
    legend_patches = []

    for i, pg in enumerate(platform_groups):
        color    = _PG_COLORS.get(pg, _FALLBACK_COLORS[i % len(_FALLBACK_COLORS)])
        col_avgs = [pg_col_vals[pg][ck][0] if pg_col_vals[pg][ck] else None for ck in col_keys]
        g_vals   = pg_all_vals[pg]
        col_avgs.append(g_vals[0] if g_vals else None)

        bar_vals = [v if v is not None else 0 for v in col_avgs]
        offset   = (i - n_pgs / 2 + 0.5) * bar_width
        bars     = ax.bar(x_pos + offset, bar_vals, width=bar_width * 0.92,
                          color=color, label=pg, zorder=3)

        for bar, val in zip(bars, col_avgs):
            if val and val > 0:
                ax.text(
                    bar.get_x() + bar.get_width() / 2,
                    bar.get_height() + 0.02,
                    str(val),
                    ha='center', va='bottom',
                    fontsize=6.5, color='#374151', fontweight='bold',
                )
        legend_patches.append(mpatches.Patch(facecolor=color, label=pg))

    ax.axvline(x=n_groups - 1.5, color='#cbd5e1', linewidth=1, linestyle='--', zorder=2)
    ax.set_xticks(x_pos)
    ax.set_xticklabels(x_labels, fontsize=8.5)
    ax.set_ylabel('Avg CT (days)', fontsize=8.5, color='#374151')
    ax.set_xlabel('Year' if use_yearly else 'Month', fontsize=8.5, color='#374151')
    ax.tick_params(axis='both', labelsize=8)
    ax.set_ylim(bottom=0)
    ax.yaxis.grid(True, color='#f1f5f9', linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color('#e2e8f0')
    ax.legend(handles=legend_patches, loc='upper right', fontsize=7.5,
              framealpha=0.9, edgecolor='#e2e8f0', ncol=min(n_pgs, 4))
    ax.set_title('Average Process CT per Platform',
                 fontsize=11, fontweight='bold', color='#1e293b', pad=8)
    plt.tight_layout(pad=1.2)

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=130, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    plt.close(fig)
    return buf


def _pdf_build_pivot1_table(pivot1, col_keys, use_yearly, page_width, margin):
    """Bangun Table reportlab untuk pivot 1."""
    fmt_cols = [pdf_format_column_label(ck, use_yearly) for ck in col_keys]
    headers  = ['Platform Gr.', 'Process Stage', 'Process Area', 'Predefined Process'] \
               + fmt_cols + ['Grand Total']
    rows     = [headers]
    styles   = _pdf_base_table_styles()
    row_idx  = 1

    for pg in sorted(pivot1):
        pg_first     = True
        stage_totals = pivot1[pg].get('_stage_totals', {})
        pg_gt        = pivot1[pg].get('_grand_total', {})

        for stage in sorted(s for s in pivot1[pg] if not s.startswith('_')):
            st_first = True

            for area in sorted(pivot1[pg][stage]):
                ar_first = True

                for proc in sorted(pivot1[pg][stage][area]):
                    d = pivot1[pg][stage][area][proc]
                    row = [
                        pg    if pg_first else '',
                        stage if st_first else '',
                        area  if ar_first else '',
                        proc,
                    ]
                    for ck in col_keys:
                        row.append(pdf_value_to_string(d.get(ck)))
                    row.append(pdf_value_to_string(d.get('grand_total')))
                    rows.append(row)
                    pg_first = st_first = ar_first = False
                    row_idx += 1

            # Subtotal stage — baca dari _stage_totals (sudah SUM di backend)
            st = stage_totals.get(stage, {})
            st_row = ['', f'Subtotal: {stage}', '', ''] \
                   + [pdf_value_to_string(st.get(ck)) for ck in col_keys] \
                   + [pdf_value_to_string(st.get('grand_total'))]
            rows.append(st_row)
            styles += [
                ('BACKGROUND', (0, row_idx), (-1, row_idx), _TABLE_COLORS['subtot']),
                ('TEXTCOLOR',  (1, row_idx), (-1, row_idx), _TABLE_COLORS['sub_val']),
                ('FONTNAME',   (1, row_idx), (-1, row_idx), 'Helvetica-Bold'),
            ]
            row_idx += 1

        # Grand total platform — baca dari _grand_total (sudah SUM di backend)
        pg_row = [f'{pg}  (Total)', 'Grand Total', '', ''] \
               + [pdf_value_to_string(pg_gt.get(ck)) for ck in col_keys] \
               + [pdf_value_to_string(pg_gt.get('grand_total'))]
        rows.append(pg_row)
        styles += [
            ('BACKGROUND', (0, row_idx), (-1, row_idx), _TABLE_COLORS['gt_bg']),
            ('TEXTCOLOR',  (0, row_idx), (-1, row_idx), _TABLE_COLORS['gt_val']),
            ('FONTNAME',   (0, row_idx), (-1, row_idx), 'Helvetica-Bold'),
        ]
        row_idx += 1

    avail   = page_width - 2 * margin - 0.2 * cm
    fixed   = 2.0*cm + 2.2*cm + 2.2*cm + 3.5*cm
    n_val   = len(col_keys) + 1
    val_w   = max((avail - fixed) / max(n_val, 1), 1.4 * cm)
    col_w   = [2.0*cm, 2.2*cm, 2.2*cm, 3.5*cm] + [val_w] * n_val

    tbl = Table(rows, repeatRows=1, colWidths=col_w)
    tbl.setStyle(TableStyle(styles))
    return tbl


def _pdf_build_pivot2_table(pivot2, platform_groups, page_width, margin):
    """Bangun Table reportlab untuk pivot 2."""
    headers = ['Process Area', 'Predefined Process'] + platform_groups + ['Grand Avg']
    rows    = [headers]
    styles  = _pdf_base_table_styles()
    row_idx = 1

    # Filter hanya area yang bukan key internal (_grand_total)
    areas = sorted(k for k in pivot2 if not k.startswith('_'))

    for area in areas:
        ar_first = True
        # Filter hanya proses yang bukan key internal (_subtotal)
        procs = sorted(k for k in pivot2[area] if not k.startswith('_'))
        subtotal = pivot2[area].get('_subtotal', {})

        for proc in procs:
            d   = pivot2[area][proc]
            row = [area if ar_first else '', proc]
            for pg in platform_groups:
                v = d.get(pg) if isinstance(d, dict) else None
                row.append(pdf_value_to_string(v))
            gt = d.get('grand_total') if isinstance(d, dict) else None
            row.append(pdf_value_to_string(gt))
            rows.append(row)
            ar_first = False
            row_idx += 1

        # Subtotal area — baca dari _subtotal backend
        ar_row = [f'Subtotal: {area}', ''] + \
                 [pdf_value_to_string(subtotal.get(pg)) for pg in platform_groups] + \
                 [pdf_value_to_string(subtotal.get('grand_total'))]
        rows.append(ar_row)
        styles += [
            ('BACKGROUND', (0, row_idx), (-1, row_idx), _TABLE_COLORS['subtot']),
            ('TEXTCOLOR',  (0, row_idx), (-1, row_idx), _TABLE_COLORS['sub_val']),
            ('FONTNAME',   (0, row_idx), (-1, row_idx), 'Helvetica-Bold'),
        ]
        row_idx += 1

    # Grand total — baca dari _grand_total backend
    grand_total = pivot2.get('_grand_total', {})
    gt_row = ['GRAND TOTAL', ''] + \
             [pdf_value_to_string(grand_total.get(pg)) for pg in platform_groups] + \
             [pdf_value_to_string(grand_total.get('grand_total'))]
    rows.append(gt_row)
    styles += [
        ('BACKGROUND', (0, row_idx), (-1, row_idx), _TABLE_COLORS['gt_bg']),
        ('TEXTCOLOR',  (0, row_idx), (-1, row_idx), _TABLE_COLORS['gt_val']),
        ('FONTNAME',   (0, row_idx), (-1, row_idx), 'Helvetica-Bold'),
    ]

    avail  = page_width - 2 * margin - 0.2 * cm
    fixed  = 2.8*cm + 3.8*cm
    n_val  = len(platform_groups) + 1
    val_w  = max((avail - fixed) / max(n_val, 1), 1.6 * cm)
    col_w  = [2.8*cm, 3.8*cm] + [val_w] * n_val

    tbl = Table(rows, repeatRows=1, colWidths=col_w)
    tbl.setStyle(TableStyle(styles))
    return tbl


# ======================================================
# PDF GENERATION — public entry point
# ======================================================

def generate_pdf(data, filters):
    """
    Generate PDF Cycle Time Analysis Dashboard (3 halaman).

    Args:
        data    : dict dari compute_ct_analysis()
        filters : dict dengan keys: year, platforms, stages, areas, processes
    Returns:
        io.BytesIO buffer containing PDF
    """
    pivot1          = data.get('pivot1', {})
    pivot2          = data.get('pivot2', {})
    col_keys        = data.get('col_keys', [])
    platform_groups = data.get('platform_groups', [])
    use_yearly      = data.get('use_yearly', True)

    MARGIN = 0.8 * cm
    PAGE_W, _PAGE_H = landscape(A4)

    buffer = io.BytesIO()
    doc    = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN + 0.2*cm, bottomMargin=MARGIN,
    )
    styles   = _pdf_make_styles()
    elements = []

    # ── Header ────────────────────────────────────────────────────────
    elements.append(Paragraph('Cycle Time Analysis Dashboard', styles['title']))
    info_parts = [f"Generated: {datetime.now().strftime('%d %B %Y, %H:%M')}"]
    if filters.get('year'):
        info_parts.append(f"Year: {filters['year']}")
    if filters.get('platforms'):
        info_parts.append(f"Platform: {', '.join(filters['platforms'])}")
    if filters.get('stages'):
        info_parts.append(f"Stage: {', '.join(filters['stages'])}")
    if filters.get('areas'):
        info_parts.append(f"Area: {', '.join(filters['areas'])}")
    if filters.get('processes'):
        info_parts.append(f"Process: {', '.join(filters['processes'])}")
    elements.append(Paragraph(' | '.join(info_parts), styles['subtitle']))

    # ── Page 1: Chart ─────────────────────────────────────────────────
    elements.append(Paragraph('1. Average Process CT per Platform', styles['section']))
    chart_buf = _pdf_build_chart(pivot1, platform_groups, col_keys, use_yearly)
    if chart_buf:
        avail_w = PAGE_W - 2 * MARGIN - 0.4 * cm
        elements.append(RLImage(chart_buf, width=avail_w, height=avail_w * 0.38))
    else:
        elements.append(Paragraph('No chart data available with current filters.', styles['note']))
    elements.append(PageBreak())

    # ── Page 2: Pivot 1 ───────────────────────────────────────────────
    col_label = 'per Year' if use_yearly else 'per Month'
    elements.append(Paragraph(f'2. Process CT {col_label}', styles['section']))
    if pivot1:
        elements.append(_pdf_build_pivot1_table(pivot1, col_keys, use_yearly, PAGE_W, MARGIN))
    else:
        elements.append(Paragraph('No data for Pivot 1 with current filters.', styles['note']))
    elements.append(PageBreak())

    # ── Page 3: Pivot 2 ───────────────────────────────────────────────
    elements.append(Paragraph('3. Process per Platform Group', styles['section']))
    if pivot2:
        elements.append(_pdf_build_pivot2_table(pivot2, platform_groups, PAGE_W, MARGIN))
    else:
        elements.append(Paragraph('No data for Pivot 2 with current filters.', styles['note']))

    doc.build(elements)
    buffer.seek(0)
    return buffer