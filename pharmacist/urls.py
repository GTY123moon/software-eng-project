from django.urls import path

from . import views

app_name = 'pharmacist'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
]
