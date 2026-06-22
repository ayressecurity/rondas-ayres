from django.urls import path

from . import views

app_name = "checkpoints"

urlpatterns = [
    path("", views.index, name="index"),
]
