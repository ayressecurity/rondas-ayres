from django.urls import path

from . import views

app_name = "escaner"

urlpatterns = [
    path("", views.index, name="index"),
    path("registrar/", views.registrar, name="registrar"),
]
