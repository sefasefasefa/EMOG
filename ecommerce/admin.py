from django.contrib import admin
from .models import (
    Category, Product, ProductImage, ProductAttribute,
    Tag, Cart, CartItem, Order, OrderItem,
    Wishlist, Review, Coupon
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active", "parent"]
    list_filter = ["is_active", "parent"]
    search_fields = ["name", "description"]
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ["parent"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        "name", "category", "price", "is_active",
        "is_featured", "stock_quantity", "discount_percentage"
    ]
    list_filter = ["is_active", "is_featured", "category"]
    search_fields = ["name", "description", "short_description"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["discount_percentage", "created_at", "updated_at"]
    filter_horizontal = ["tags"]
    fieldsets = (
        (None, {"fields": ("name", "slug", "category")}),
        ("Pricing", {"fields": (
            "price", "compare_at_price", "discount_percentage"
        )}),
        ("Content", {"fields": ("description", "short_description")}),
        ("Inventory", {"fields": (
            "sku", "stock_quantity", "min_order_quantity",
            "track_inventory", "is_active"
        )}),
        ("Features", {"fields": ("is_featured", "tags")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ["product", "is_primary", "display_order", "created_at"]
    list_filter = ["is_primary"]
    raw_id_fields = ["product"]
    list_editable = ["display_order", "is_primary"]


@admin.register(ProductAttribute)
class ProductAttributeAdmin(admin.ModelAdmin):
    list_display = ["product", "name", "value", "is_filterable"]
    list_filter = ["is_filterable"]
    raw_id_fields = ["product"]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["cart_id", "created_at", "updated_at"]
    readonly_fields = ["cart_id", "created_at", "updated_at"]


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ["cart", "product", "quantity", "price_at_addition"]
    raw_id_fields = ["cart", "product"]


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        "id", "user", "status", "payment_method",
        "total_amount", "created_at"
    ]
    list_filter = ["status", "payment_method", "created_at"]
    search_fields = ["id", "user__email"]
    readonly_fields = ["total_amount", "created_at", "updated_at"]
    fieldsets = (
        (None, {"fields": ("user", "cart_session", "status", "payment_method")}),
        ("Financial", {"fields": (
            "total_amount", "tax_amount", "discount_amount"
        )}),
        ("Addresses", {"fields": ("shipping_address", "billing_address")}),
        ("Notes & Tracking", {"fields": ("notes", "tracking_number")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["order", "product", "quantity", "price", "total"]
    raw_id_fields = ["order", "product"]


@admin.register(Wishlist)
class WishlistAdmin(admin.ModelAdmin):
    list_display = ["user", "product", "added_at"]
    raw_id_fields = ["user", "product"]
    list_filter = ["added_at"]


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["product", "user", "rating", "is_verified_purchase", "created_at"]
    list_filter = ["rating", "is_verified_purchase", "created_at"]
    raw_id_fields = ["product", "user"]
    search_fields = ["comment", "title"]


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ["code", "is_active", "times_used", "valid_from", "valid_to"]
    list_filter = ["is_active", "valid_from", "valid_to"]
    search_fields = ["code"]
    readonly_fields = ["times_used"]