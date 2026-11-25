from django.urls import path
from . import views

app_name = 'accounts'

urlpatterns = [
    # 平台入口：身份选择
    path('role/', views.role_select_view, name='role_select'),
    # 患者登录 / 注册页
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    # 职工（医生/药师/管理员）内部密钥登录：统一入口 + 按角色固定入口（兼容旧链接）
    path('staff/', views.staff_entry_view, name='staff_entry'),
    path('staff/<str:role>/', views.staff_login_view, name='staff_login'),
    # 退出登录
    path('logout/', views.logout_view, name='logout'),
    # 患者工作台
    path('', views.dashboard_view, name='dashboard'),
    path('dashboard/', views.dashboard_view, name='dashboard_alt'),
    # 个人中心 / 健康档案
    path('profile/', views.profile_view, name='profile'),
    # 发起问诊
    path('consult/create/', views.consult_create_view, name='consult_create'),
    # AI 引导式预问诊评估
    path('consult/<int:consult_id>/assess/', views.consult_assess_view, name='consult_assess'),
    # 消息记录
    path('messages/', views.messages_view, name='messages'),
    # 问诊聊天
    path('consult/<int:consult_id>/', views.consult_chat_view, name='consult_chat'),
    # 排队状态轮询（JSON）
    path('consult/<int:consult_id>/status/', views.consult_status_view, name='consult_status'),
    # 对话态消息轮询（JSON，自动加载医生新回复）
    path('consult/<int:consult_id>/messages/', views.consult_messages_view, name='consult_messages'),
    # 音视频通话信令轮询/上报（JSON）
    path('consult/<int:consult_id>/call-signal/', views.call_signal_view, name='call_signal'),
    # 我的处方
    path('prescriptions/', views.prescription_list_view, name='prescription_list'),
    # 处方详情
    path('prescriptions/<int:presc_id>/', views.prescription_detail_view, name='prescription_detail'),
    # 患者选择取药方式与代煎（审核通过后）
    path('prescriptions/<int:presc_id>/pickup/', views.prescription_pickup_view, name='prescription_pickup'),
]
