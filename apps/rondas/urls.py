from django.urls import path

from . import views

app_name = "rondas"

urlpatterns = [
    path("", views.index, name="index"),
    path("nueva/", views.nueva, name="nueva"),
    path("editar/<int:pk>/", views.editar, name="editar"),
    path("eliminar/<int:pk>/", views.eliminar, name="eliminar"),
]
