from django.urls import path

from . import views

app_name = "rondas"

urlpatterns = [
    path("", views.index, name="index"),
]
