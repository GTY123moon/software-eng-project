"""医生端路由（app_name = 'doctor'，主路由已挂在 /doctor/ 前缀下）。

登录沿用 main 的统一职工入口 accounts:staff_login(role='doctor')，
本文件不再提供医生端独立登录页；入口名保持 dashboard，
以便 accounts.STAFF_ROLES 里的 reverse('doctor:dashboard') 直接可用。
"""

from django.urls import path

from . import views

app_name = 'doctor'

urlpatterns = [
    # 工作台（登录后由 accounts:staff_login 重定向到此）
    path('', views.workbench, name='dashboard'),

    # 接诊与问诊详情（预问诊：患者档案 + AI 摘要复核）
    path('consult/<int:cid>/', views.consult_detail, name='consult_detail'),
    path('consult/<int:cid>/chat/', views.consult_chat_view, name='consult_chat'),
    path('consult/<int:cid>/accept/', views.consult_accept, name='consult_accept'),
    path('consult/<int:cid>/review/', views.review_ai, name='review_ai'),
    path('consult/<int:cid>/ai-suggest/', views.ai_suggest, name='ai_suggest'),
    path('consult/<int:cid>/message/', views.send_message, name='send_message'),
    path('consult/<int:cid>/end/', views.end_consult, name='end_consult'),

    # 处方：按问诊新建草稿 / 编辑草稿 / 列表 / 提交审核
    path('consult/<int:cid>/prescription/', views.prescription_new, name='prescription_new'),
    path('prescriptions/', views.prescription_list, name='prescription_list'),
    path('prescriptions/<int:presc_id>/edit/', views.prescription_edit, name='prescription_edit'),
    path('prescriptions/<int:presc_id>/submit/', views.prescription_submit, name='prescription_submit'),
]
