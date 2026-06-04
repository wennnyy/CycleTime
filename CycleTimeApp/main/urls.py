from django.urls import path
from . import views

urlpatterns = [
    # ── Auth ──────────────────────────────────────────────────────────────────
    path('',        views.login_view,  name='home'),
    path('login/',  views.login_view,  name='login'),
    path('logout/', views.logout_view, name='logout'),

    # ── Dashboard (entry point per role) ──────────────────────────────────────
    path('dashboard/admin/',      views.dashboard_admin,      name='dashboard_admin'),
    path('dashboard/staff/',      views.dashboard_staff,      name='dashboard_staff'),
    path('dashboard/management/', views.dashboard_management, name='dashboard_management'),

    # ── Admin: Sync ───────────────────────────────────────────────────────────
    # FIX: path 'admin_sync' sebelumnya terduplikasi dua kali (baris 16 & 27).
    # Duplikasi ini menyebabkan Django hanya mendaftarkan route pertama dan
    # mengabaikan yang kedua — membuang-buang namespace dan membingungkan.
    path('dashboard/admin/sync/', views.admin_sync, name='admin_sync'),

    # ── Admin: Data Management ────────────────────────────────────────────────
    path('dashboard/admin/data/', views.admin_data, name='admin_data'),

    # ── Admin: Users ──────────────────────────────────────────────────────────
    path('dashboard/admin/users/',                    views.admin_users, name='admin_users'),
    path('dashboard/admin/users/add/',                views.add_user,    name='add_user'),
    path('dashboard/admin/users/edit/<int:user_id>/', views.edit_user,   name='edit_user'),
    path('dashboard/admin/users/delete/<int:user_id>/', views.delete_user, name='delete_user'),

    # ── Admin: Reports ────────────────────────────────────────────────────────
    path('dashboard/admin/reports/',                 views.admin_reports, name='admin_reports'),
    path('dashboard/admin/reports/download/excel/',  views.download_report_excel, name='download_report_excel'),

    # ── Excel Report Download (All Roles) ──────────────────────────────────────
    # All roles use the same generic downloader with role-based access control
    path('dashboard/staff/reports/download/excel/', views.download_report_excel, name='download_report_excel_staff'),
    path('dashboard/management/reports/download/excel/', views.download_report_excel, name='download_report_excel_management'),

    # ── Staff ─────────────────────────────────────────────────────────────────
    path('dashboard/staff/view-data/', views.staff_view_data, name='staff_view_data'),
    path('dashboard/staff/reports/',   views.staff_reports,   name='staff_reports'),

    # ── Management ────────────────────────────────────────────────────────────
    path('dashboard/management/reports/', views.management_reports, name='management_reports'),

    # ── Shared API Endpoints ──────────────────────────────────────────────────
    path('dashboard/chart-data/',        views.chart_data_api,      name='chart_data_api'),

    # ── Cycle Time Analysis ───────────────────────────────────────────────────
    # FIX: ct_analysis_dashboard ada di views.py tapi TIDAK ADA URL-nya.
    # Tanpa URL ini, fungsi tersebut tidak dapat diakses sama sekali.
    path('dashboard/ct-analysis/',       views.ct_analysis_dashboard, name='ct_analysis_dashboard'),
    path('dashboard/ct-analysis/download/pdf/', views.download_dashboard_pdf, name='download_dashboard_pdf'),
    # convenience routes so staff/management dashboards can call the same PDF generator
    path('dashboard/ct-analysis/data/',  views.ct_analysis_data,      name='ct_analysis_data'),
]