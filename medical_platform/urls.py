"""
URL configuration for medical_platform project.

主路由：把不同前缀分发到各业务 app 的 urls.py。
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('doctor/', include('doctor.urls')),
    path('pharmacist/', include('pharmacist.urls')),
    path('adminpanel/', include('adminpanel.urls')),
]
