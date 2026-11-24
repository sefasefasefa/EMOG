from django.urls import path
from . import views

app_name = "ecommerce"

urlpatterns = [
    # Ana sayfa
    path("", views.product_list, name="product_list"),

    # Kategori bazlı ürün listesi
    path("category/<slug:category_slug>/",
         views.product_list_by_category, name="product_list_by_category"),

    # Arama
    path("search/", views.product_search, name="product_search"),

    # Ürün detay
    path("product/<int:product_id>/<slug:slug>/",
         views.product_detail, name="product_detail"),

    # Sepet sayfaları
    path("cart/", views.cart_detail, name="cart_detail"),
    path("cart/add/<int:product_id>/",
         views.cart_add, name="cart_add"),
    path("cart/remove/<int:cart_item_id>/",
         views.cart_remove, name="cart_remove"),

    # Sipariş sayfaları
    path("checkout/", views.checkout, name="checkout"),
    path("order/thank-you/<int:order_id>/",
         views.order_thank_you, name="order_thank_you"),

    # Wishlist
    path("wishlist/add/<int:product_id>/",
         views.wishlist_add, name="wishlist_add"),
    path("wishlist/", views.wishlist_detail, name="wishlist_detail"),

    # Reviews
    path("product/<int:product_id>/review/",
         views.product_review, name="product_review"),

    # API endpoints (AI integration points)
    path("api/recommendations/",
         views.ai_recommendations, name="ai_recommendations"),
]