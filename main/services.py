# ============================================================
# main/services.py
# ============================================================

import logging
import requests

from django.conf    import settings
from django.utils   import timezone

from main.models         import RawTicket, SyncLog
from main.process_groups import enrich_ticket, get_platform
from main.utils          import hitung_cycle_time

logger = logging.getLogger(__name__)

_BASE_URL  = settings.MOCK_JIRA_BASE_URL
_TIMEOUT   = settings.MOCK_JIRA_TIMEOUT
_PAGE_SIZE = settings.MOCK_JIRA_PAGE_SIZE
_DIRECT_DB = settings.MOCK_JIRA_USE_DIRECT_DB
_URL_MAIN  = f"{_BASE_URL}/issues/"
_URL_SUB   = f"{_BASE_URL}/sub-issues/"
_META_KEYS = frozenset({'total', 'page', 'page_size', 'total_pages'})


# ======================================================
# API: PAGINATED FETCH
# ======================================================
def ambil_semua_halaman(url, params, timeout=None):
    hasil    = []
    page     = 1
    key_data = None
    if timeout is None:
        timeout = _TIMEOUT

    while True:
        params['page']      = page
        params['page_size'] = _PAGE_SIZE

        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()

        if key_data is None:
            for k in data:
                if k not in _META_KEYS:
                    key_data = k
                    break

        items       = data.get(key_data, [])
        hasil      += items
        total_pages = data.get('total_pages', 1)

        if page >= total_pages:
            break
        page += 1

    return hasil


# ======================================================
# QUERY: HITUNG RECORDS TERSEDIA
# ======================================================
def hitung_available_records(start_date, end_date):
    if _DIRECT_DB:
        try:
            from mock_jira.models import JiraSubTicket
            total = JiraSubTicket.objects.filter(
                status='Completed',
                due_date__gte=start_date,
                due_date__lte=end_date,
            ).count()
            return total, None
        except Exception as e:
            logger.warning(f"[available_records] Direct DB query gagal: {e}")
            return 0, f"Tidak bisa menghitung records: {str(e)}"
    else:
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


# ======================================================
# SERVICE: SYNC JIRA DATA
# ======================================================
class SyncResult:
    """
    Attributes:
        success         : True jika sync selesai tanpa exception fatal
        total_fetched   : jumlah sub-ticket yang diambil dari API
        total_processed : jumlah ticket BARU yang berhasil di-insert ke DB
        total_skipped   : jumlah ticket yang di-skip karena sudah ada di DB
        error_type      : 'connection' | 'timeout' | 'empty' | 'unknown' | None
        error_detail    : pesan error mentah
    """
    def __init__(self):
        self.success         = False
        self.total_fetched   = 0
        self.total_processed = 0
        self.total_skipped   = 0
        self.error_type      = None
        self.error_detail    = None


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
        # ── Step 1: Fetch sub-tickets ─────────────────────────────────────
        all_sub = ambil_semua_halaman(
            url=_URL_SUB,
            params={
                'due_after':  start_date,
                'due_before': end_date,
                'status':     'Completed',
            },
        )
        result.total_fetched = len(all_sub)
        logger.info(f"[Sync #{sync_log.id}] Fetched {result.total_fetched} sub-tickets")

        if not all_sub:
            sync_log.finished_at = timezone.now()
            sync_log.status      = 'success'
            sync_log.save()
            result.success    = True
            result.error_type = 'empty'
            return sync_log, result

        # ── Step 2: Kumpulkan parent keys ─────────────────────────────────
        all_parent_keys = set(
            sub['fields']['parent']['key']
            for sub in all_sub
            if sub.get('fields', {}).get('parent')
        )

        # ── Step 3: Fetch main tickets via HTTP API ───────────────────────
        main_map = {}
        if all_parent_keys:
            try:
                all_main_raw = ambil_semua_halaman(url=_URL_MAIN, params={})
                main_map = {
                    m['key']: m['fields']
                    for m in all_main_raw
                    if m['key'] in all_parent_keys
                }
                logger.info(
                    f"[Sync #{sync_log.id}] Resolved "
                    f"{len(main_map)}/{len(all_parent_keys)} parent keys via API"
                )
            except requests.exceptions.RequestException as e:
                logger.warning(f"[Sync #{sync_log.id}] Gagal fetch main issues via API: {e}")

            if _DIRECT_DB:
                missing_keys = all_parent_keys - set(main_map.keys())
                if missing_keys:
                    try:
                        from mock_jira.models import JiraMainTicket
                        db_parents = JiraMainTicket.objects.filter(
                            issue_key__in=missing_keys
                        ).values('issue_key', 'package')
                        for parent in db_parents:
                            main_map[parent['issue_key']] = {'package': parent['package']}
                        logger.info(
                            f"[Sync #{sync_log.id}] Resolved "
                            f"{len(db_parents)} missing keys via DB fallback"
                        )
                    except Exception as e:
                        logger.warning(f"[Sync #{sync_log.id}] DB fallback gagal: {e}")

        # ── Step 4: Bangun objek RawTicket ────────────────────────────────
        main_objects = []
        for key, fields in main_map.items():
            package    = fields.get('package', '')
            status_raw = fields.get('status', '')
            status_val = (
                status_raw.get('name', '') if isinstance(status_raw, dict) else status_raw
            )
            main_objects.append(RawTicket(
                ticket_key         = key,
                parent_key         = None,
                platform           = get_platform(package),
                summary            = f"Main ticket {key}",
                status             = status_val,
                start_date         = None,
                due_date           = None,
                cycle_time         = None,
                package_name       = package,
                predefined_process = None,
                sync_log           = sync_log,
            ))

        sub_objects = []
        for sub in all_sub:
            sf      = sub['fields']
            spk     = sf.get('parent', {}).get('key', '')
            pf      = main_map.get(spk, {})
            package = pf.get('package', '')
            proc    = sf.get('predefined_process', '') or ''

            ct       = hitung_cycle_time(sf.get('start_date'), sf.get('due_date'))
            enriched = enrich_ticket({
                'platform':           package,
                'predefined_process': proc,
            })

            status_raw = sf.get('status', '')
            status_val = (
                status_raw.get('name', '') if isinstance(status_raw, dict) else status_raw
            )
            sub_objects.append(RawTicket(
                ticket_key         = sub['key'],
                parent_key         = spk,
                platform           = enriched['platform_group'],
                summary            = f"{proc} - {spk}" if proc and spk else sub['key'],
                status             = status_val,
                start_date         = sf.get('start_date') or None,
                resolved_date      = sf.get('resolved_date') or None,
                due_date           = sf.get('due_date') or None,
                cycle_time         = ct,
                package_name       = package,
                predefined_process = proc,
                sync_log           = sync_log,
            ))

        # ── Step 5: Filter — hanya ticket BARU yang belum ada di DB ──────
        #
        # SKIP-IF-EXISTS:
        # 1. Ambil semua ticket_key dari batch
        # 2. Query DB → mana yang sudah ada
        # 3. Buang yang sudah ada → hanya INSERT yang benar-benar baru
        # 4. Data lama TIDAK DISENTUH, TIDAK muncul di tabel sync
        # ──────────────────────────────────────────────────────────────────
        all_objects = main_objects + sub_objects
        all_keys    = [obj.ticket_key for obj in all_objects]

        existing_keys = set(
            RawTicket.objects
            .filter(ticket_key__in=all_keys)
            .values_list('ticket_key', flat=True)
        )

        new_objects          = [obj for obj in all_objects if obj.ticket_key not in existing_keys]
        result.total_skipped = len(all_objects) - len(new_objects)

        logger.info(
            f"[Sync #{sync_log.id}] "
            f"{len(new_objects)} baru akan di-insert, "
            f"{result.total_skipped} sudah ada di DB (di-skip)"
        )

        # ── Step 6: Bulk INSERT hanya ticket baru ─────────────────────────
        # ignore_conflicts=True sebagai safety net race condition —
        # TIDAK mengupdate data lama sama sekali.
        BATCH_SIZE = 500
        for i in range(0, len(new_objects), BATCH_SIZE):
            RawTicket.objects.bulk_create(
                new_objects[i:i + BATCH_SIZE],
                ignore_conflicts = True,
                batch_size       = BATCH_SIZE,
            )

        result.total_processed = len(new_objects)
        logger.info(
            f"[Sync #{sync_log.id}] Done — "
            f"{result.total_processed} ticket baru disimpan, "
            f"{result.total_skipped} dilewati"
        )

        # ── Step 7: Update SyncLog ────────────────────────────────────────
        sync_log.finished_at     = timezone.now()
        sync_log.total_fetched   = result.total_fetched
        sync_log.total_processed = result.total_processed
        sync_log.total_errors    = result.total_skipped  # pakai kolom ini untuk skipped
        sync_log.status          = 'success'
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