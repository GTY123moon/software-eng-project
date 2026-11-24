from django.urls import path

from . import views

app_name = 'adminpanel'

urlpatterns = [
    # 首页概览
    path('', views.dashboard, name='dashboard'),

    # 药品目录管理
    path('drugs/', views.drug_list, name='drug_list'),
    path('drugs/add/', views.drug_form, name='drug_add'),
    path('drugs/<int:drug_id>/edit/', views.drug_form, name='drug_edit'),
    path('drugs/<int:drug_id>/delete/', views.drug_delete, name='drug_delete'),

    # 值班排班管理
    path('duties/', views.duty_list, name='duty_list'),
    path('duties/add/', views.duty_form, name='duty_add'),
    path('duties/<int:duty_code>/edit/', views.duty_form, name='duty_edit'),

    # 日志审计
    path('logs/', views.log_list, name='log_list'),
    path('logs/<int:log_id>/', views.log_detail, name='log_detail'),
    path('referrals/', views.referral_list, name='referral_list'),

    # 账号管理
    path('accounts/', views.account_list, name='account_list'),
    path('accounts/add/', views.account_form, name='account_add'),
    path('accounts/<int:user_id>/edit/', views.account_edit, name='account_edit'),
    path('accounts/<int:user_id>/toggle/', views.account_toggle, name='account_toggle'),
]
