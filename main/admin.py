from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    model = User

    # List view
    list_display = (
        'username',
        'email',
        'role',
        'is_staff',
        'is_active',
        'is_superuser',
    )
    list_filter = ('role', 'is_staff', 'is_active')

    # Edit user
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('email',)}),
        ('Role Information', {'fields': ('role',)}),
        ('Permissions', {
            'fields': (
                'is_active',
                'is_staff',
                'is_superuser',
                'groups',
                'user_permissions',
            )
        }),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    # ADD USER FORM (INI KUNCINYA 🔑)
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': (
                'username',
                'password1',
                'password2',
                'role',
                'is_staff',
                'is_active',
            ),
        }),
    )

    search_fields = ('username', 'email')
    ordering = ('username',)
