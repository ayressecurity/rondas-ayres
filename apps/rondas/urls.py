from django.urls import path

from . import views

app_name = "rondas"

urlpatterns = [
    path("", views.index, name="index"),
    path("escaner/", views.escaner, name="escaner"),
    path("escaner/registrar/", views.escaner_registrar, name="escaner_registrar"),
]
