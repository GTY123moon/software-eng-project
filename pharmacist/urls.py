from django.urls import path

from . import views

app_name = 'pharmacist'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('prescriptions/', views.prescription_list, name='prescription_list'),
    path('prescriptions/pending-review/', views.prescription_list,
         {'section': 'review'}, name='pending_review'),
    path('prescriptions/pending-dispense/', views.prescription_list,
         {'section': 'dispense'}, name='pending_dispense'),
    path('prescriptions/processed/', views.prescription_list,
         {'section': 'processed'}, name='processed'),
    path('prescriptions/<int:presc_id>/', views.prescription_detail,
         name='prescription_detail'),
    path('prescriptions/<int:presc_id>/audit/', views.prescription_audit,
         name='prescription_audit'),
    path('prescriptions/<int:presc_id>/dispense/', views.prescription_dispense,
         name='prescription_dispense'),
]
