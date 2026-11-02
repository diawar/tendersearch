from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, re_path
from django.http import HttpResponseForbidden

from tender_search.core.views import dashboard, health
from tender_search.core import views
from tender_search.core import lead_views
from tender_search.core import user_admin

urlpatterns = [
    path("", dashboard, name="dashboard"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    re_path(r"^admin(?:/.*)?$", lambda request: HttpResponseForbidden("Администрирование временно отключено."), name="admin_disabled"),
    path("health/", health, name="health"),
    path("search/", views.search, name="search"),
    path("search/index-status/", views.index_status, name="index_status"),
    path("profiles/<int:profile_id>/delete/", views.profile_delete, name="profile_delete"),
    path("search/<uuid:job_id>/cancel/", views.search_cancel, name="search_cancel"),
    path("search/<uuid:job_id>/", views.search_results, name="search_results"),
    path("lots/<int:lot_id>/", views.lot_detail, name="lot_detail"),
    path("lots/<int:lot_id>/refresh/", views.lot_refresh, name="lot_refresh"),
    path("lots/<int:lot_id>/favorite/", views.favorite, name="favorite"),
    path("settings/", views.notification_settings, name="notification_settings"),
    path("administration/", user_admin.users, name="administration_users"),
    path("administration/users/<int:user_id>/edit/", user_admin.edit_user, name="administration_user_edit"),
    path("administration/users/<int:user_id>/delete/", user_admin.delete_user, name="administration_user_delete"),
    path("administration/clients/", lead_views.leads, name="administration_leads"),
    path("administration/collection-status/", lead_views.collection_status, name="lead_collection_status"),
    path("administration/clients/export/", lead_views.export_leads, name="administration_leads_export"),
    path("administration/clients/<int:company_id>/", lead_views.lead_detail, name="administration_lead_detail"),
    path("profiles/<int:profile_id>/notifications/", views.profile_notifications, name="profile_notifications"),
    path("lots/<int:lot_id>/status-watch/", views.status_watch, name="status_watch"),
    path("lots/<int:lot_id>/documents/retry/", views.document_retry, name="document_retry"),
    path("lots/<int:lot_id>/documents/<int:version_id>/", views.document_download, name="document_download"),
    path("favorites/", views.favorites, name="favorites"),
]
