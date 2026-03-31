from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='home'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard
    path('dashboard/admin/', views.dashboard_admin, name='dashboard_admin'),
    path('dashboard/staff/', views.dashboard_staff, name='dashboard_staff'),
    path('dashboard/management/', views.dashboard_management, name='dashboard_management'),

    # ADMIN MODULE (ubah prefix)
    path('dashboard/admin/sync/', views.admin_sync, name='admin_sync'),
    path('dashboard/admin/data/', views.admin_data, name='admin_data'),
    #admin user 
    path('dashboard/admin/users/', views.admin_users, name='admin_users'),
    path('dashboard/admin/users/add/', views.add_user, name='add_user'),
    path('dashboard/admin/users/edit/<int:user_id>/', views.edit_user, name='edit_user'),
    path('dashboard/admin/users/delete/<int:user_id>/', views.delete_user, name='delete_user'),
    path('dashboard/admin/reports/', views.admin_reports, name='admin_reports'),
    path('dashboard/admin/reports/download/', views.download_report, name='download_report'),
    #admin sync
    path('dashboard/admin/sync/', views.admin_sync, name='admin_sync'),

    # STAFF MODULE
    path('dashboard/staff/view-data/', views.staff_view_data, name='staff_view_data'),
    path('dashboard/staff/reports/', views.staff_reports, name='staff_reports'),
    
    # MANAGEMENT MODULE
    path('dashboard/management/reports/', views.management_reports, name='management_reports'),
    
    
    path('dashboard/chart-data/', views.chart_data_api, name='chart_data_api'),
    path('dashboard/ct-analysis/data/', views.ct_analysis_data, name='ct_analysis_data'),
]
