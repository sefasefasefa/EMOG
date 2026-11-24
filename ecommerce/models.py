from django.db import models
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


class Category(models.Model):
    """Product categories for e-commerce"""
    name = models.CharField("Category name", max_length=100, unique=True)
    slug = models.SlugField("Category slug", max_length=100, unique=True)
    description = models.TextField("Description", blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True,
        related_name="children", verbose_name="Parent category"
    )
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse("ecommerce:product_list_by_category", args=[self.slug])


class Product(models.Model):
    """Main product catalog"""
    name = models.CharField("Product name", max_length=200)
    slug = models.SlugField("Slug", max_length=200)
    description = models.TextField("Description")
    short_description = models.TextField("Short description", blank=True)
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True,
        related_name="products", verbose_name="Category"
    )
    author = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="products", verbose_name="Author"
    )
    price = models.DecimalField("Price", max_digits=10, decimal_places=2)
    compare_at_price = models.DecimalField(
        "Compare at price (discount)", max_digits=10, decimal_places=2,
        null=True, blank=True
    )
    sku = models.CharField("SKU", max_length=50, unique=True, blank=True)
    slug_for_seo = models.SlugField("SEO slug", max_length=200, blank=True)
    is_active = models.BooleanField("Active", default=True)
    is_featured = models.BooleanField("Featured", default=False)
    stock_quantity = models.IntegerField("Stock quantity", default=0)
    min_order_quantity = models.IntegerField("Min order qty", default=1)
    track_inventory = models.BooleanField("Track inventory", default=True)
    date_available = models.DateTimeField("Available from", null=True, blank=True)
    tags = models.ManyToManyField("Tag", related_name="products", blank=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ["-date_available", "-created_at"]
        indexes = [
            models.Index(fields=["is_active", "-price"]),
            models.Index(fields=["name", "slug"]),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse(
            "ecommerce:product_detail",
            args=[self.id, self.slug, self.category.slug if self.category else None]
        )

    @property
    def discount_percentage(self):
        if self.compare_at_price and self.price < self.compare_at_price:
            return round(100 * (1 - self.price / self.compare_at_price))
        return 0

    @property
    def is_in_stock(self):
        if self.track_inventory:
            return self.stock_quantity > 0
        return True


class ProductImage(models.Model):
    """Product images gallery"""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="images"
    )
    image = models.ImageField("Image", upload_to="product_images/")
    alt_text = models.CharField("Alt text", max_length=200, blank=True)
    is_primary = models.BooleanField("Primary image", default=False)
    display_order = models.IntegerField("Display order", default=0)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Product image"
        verbose_name_plural = "Product images"
        ordering = ["display_order", "-is_primary"]

    def __str__(self):
        return f"Image for {self.product.name}"


class ProductAttribute(models.Model):
    """Product attributes/specifications"""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="attributes"
    )
    name = models.CharField("Attribute name", max_length=100)
    value = models.CharField("Attribute value", max_length=255)
    is_filterable = models.BooleanField("Filterable", default=True)

    class Meta:
        verbose_name = "Product attribute"
        verbose_name_plural = "Product attributes"
        unique_together = ["product", "name"]

    def __str__(self):
        return f"{self.name}: {self.value}"


class Tag(models.Model):
    """Product tags for additional categorization"""
    name = models.CharField("Tag name", max_length=50, unique=True)
    slug = models.SlugField("Tag slug", max_length=50, unique=True)

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Cart(models.Model):
    """Shopping cart for unauthenticated users"""
    cart_id = models.CharField("Cart ID", max_length=250, unique=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)

    class Meta:
        verbose_name = "Cart"
        verbose_name_plural = "Carts"

    def __str__(self):
        return f"Cart {self.cart_id[:12]}"


class CartItem(models.Model):
    """Items in shopping cart"""
    cart = models.ForeignKey(
        Cart, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="cart_items"
    )
    quantity = models.IntegerField("Quantity", default=1)
    price_at_addition = models.DecimalField(
        "Price at addition", max_digits=10, decimal_places=2
    )
    added_at = models.DateTimeField("Added at", auto_now_add=True)

    class Meta:
        verbose_name = "Cart item"
        verbose_name_plural = "Cart items"
        unique_together = ["cart", "product"]

    def __str__(self):
        return f"{self.product.name} (x{self.quantity})"


class Order(models.Model):
    """Customer orders"""
    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("shipped", "Shipped"),
        ("delivered", "Delivered"),
        ("cancelled", "Cancelled"),
        ("refunded", "Refunded"),
    ]

    PAYMENT_METHOD_CHOICES = [
        ("credit_card", "Credit Card"),
        ("paypal", "PayPal"),
        ("bank_transfer", "Bank Transfer"),
        ("cash_on_delivery", "Cash on Delivery"),
        ("emi", "EMI/Installments"),
    ]

    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="orders", verbose_name="User"
    )
    cart_session = models.CharField(
        "Cart session", max_length=250, null=True, blank=True
    )
    status = models.CharField(
        "Status", max_length=20, choices=STATUS_CHOICES, default="pending"
    )
    payment_method = models.CharField(
        "Payment method", max_length=20,
        choices=PAYMENT_METHOD_CHOICES, null=True, blank=True
    )
    total_amount = models.DecimalField("Total amount", max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField("Tax amount", max_digits=10, decimal_places=2, default=0)
    discount_amount = models.DecimalField(
        "Discount amount", max_digits=10, decimal_places=2, default=0
    )
    shipping_address = models.TextField("Shipping address", blank=True)
    billing_address = models.TextField("Billing address", blank=True)
    notes = models.TextField("Order notes", blank=True)
    tracking_number = models.CharField(
        "Tracking number", max_length=100, null=True, blank=True
    )
    created_at = models.DateTimeField("Created at", auto_now_add=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Order {self.id} - {self.status}"

    @property
    def grand_total(self):
        return self.total_amount - self.discount_amount + self.tax_amount


class OrderItem(models.Model):
    """Items in each order"""
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items"
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True
    )
    quantity = models.IntegerField("Quantity", default=1)
    price = models.DecimalField("Price", max_digits=10, decimal_places=2)
    total = models.DecimalField("Total", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Order item"
        verbose_name_plural = "Order items"

    def __str__(self):
        return f"{self.product.name} (x{self.quantity})" if self.product else "Removed item"


class Wishlist(models.Model):
    """User's wishlist/saved products"""
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="wishlists"
    )
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE
    )
    added_at = models.DateTimeField("Added at", auto_now_add=True)

    class Meta:
        verbose_name = "Wishlist item"
        verbose_name_plural = "Wishlist items"
        unique_together = ["user", "product"]

    def __str__(self):
        return f"{self.product.name} - {self.user.email}"


class Review(models.Model):
    """Product reviews and ratings"""
    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="reviews"
    )
    user = models.ForeignKey(
        User, on_delete=models.CASCADE
    )
    rating = models.IntegerField("Rating", choices=[
        (1, "1 Star"), (2, "2 Stars"), (3, "3 Stars"),
        (4, "4 Stars"), (5, "5 Stars")
    ])
    title = models.CharField("Review title", max_length=200, blank=True)
    comment = models.TextField("Comment")
    is_verified_purchase = models.BooleanField("Verified purchase", default=False)
    created_at = models.DateTimeField("Created at", auto_now_add=True)
    updated_at = models.DateTimeField("Updated at", auto_now=True)

    class Meta:
        verbose_name = "Review"
        verbose_name_plural = "Reviews"
        unique_together = ["product", "user"]

    def __str__(self):
        return f"Review by {self.user.email} on {self.product.name}"


class Coupon(models.Model):
    """Discount coupons/promotional codes"""
    code = models.CharField("Coupon code", max_length=50, unique=True)
    description = models.TextField("Description", blank=True)
    amount_or_percentage = models.CharField(
        "Amount or percentage", max_length=10
    )
    value = models.DecimalField("Value", max_digits=10, decimal_places=2)
    minimum_order_amount = models.DecimalField(
        "Minimum order amount", max_digits=10, decimal_places=2, default=0
    )
    maximum_discount_amount = models.DecimalField(
        "Maximum discount", max_digits=10, decimal_places=2, default=0
    )
    usage_limit = models.IntegerField("Usage limit", null=True, blank=True)
    times_used = models.IntegerField("Times used", default=0)
    valid_from = models.DateTimeField("Valid from")
    valid_to = models.DateTimeField("Valid to")
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField("Created at", auto_now_add=True)

    class Meta:
        verbose_name = "Coupon"
        verbose_name_plural = "Coupons"

    def __str__(self):
        return self.code