"""
URL configuration for medical_platform project.

主路由：把不同前缀分发到各业务 app 的 urls.py。
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic.base import RedirectView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # 根路径默认跳转到患者登录页
    path('', RedirectView.as_view(pattern_name='accounts:login')),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('doctor/', include('doctor.urls')),
    path('pharmacist/', include('pharmacist.urls')),
    path('adminpanel/', include('adminpanel.urls')),
]

# 开发环境下提供上传媒体文件（聊天图片）的访问
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
