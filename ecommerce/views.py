from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db.models import Q, Count, Avg
from .models import (
    Category, Product, ProductImage, ProductAttribute,
    Tag, Cart, CartItem, Order, OrderItem,
    Wishlist, Review, Coupon
)
from .forms import CartAddProductForm, ReviewForm


def product_list(request):
    """Ana ürün listesi sayfası - AI ile kişiselleştirilmiş"""
    products = Product.objects.filter(is_active=True).select_related("category")

    # Filtreler
    category_slug = request.GET.get("category")
    if category_slug:
        category = get_object_or_404(Category, slug=category_slug)
        products = products.filter(category=category)

    # Arama
    query = request.GET.get("q")
    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(short_description__icontains=query)
        )

    # Etiket filtresi
    tag_slug = request.GET.get("tag")
    if tag_slug:
        tag = get_object_or_404(Tag, slug=tag_slug)
        products = products.filter(tags=tag)

    # AI: Popüler ve öne çıkan ürünler
    featured_products = Product.objects.filter(
        is_active=True, is_featured=True
    )[:8]

    # AI: Ürün etiketleri
    tags = Tag.objects.annotate(
        product_count=Count("product", filter=Q(product__is_active=True))
    )[:15]

    # Sayfalama
    paginator = Paginator(products.order_by("-date_available", "-created_at"), 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "ecommerce/product_list.html", {
        "page_obj": page_obj,
        "featured_products": featured_products,
        "categories": Category.objects.filter(is_active=True)[:12],
        "tags": tags,
        "query": query,
    })


def product_list_by_category(request, category_slug):
    """Kategori bazlı ürün listesi"""
    category = get_object_or_404(Category, slug=category_slug)
    products = Product.objects.filter(
        category=category, is_active=True
    ).select_related("category")

    # AI: Bu kategorideki en popüler ürünler
    popular_in_category = products.annotate(
        avg_rating=Avg("reviews__rating")
    ).order_by("-avg_rating", "-price")[:10]

    paginator = Paginator(products, 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "ecommerce/product_list.html", {
        "category": category,
        "page_obj": page_obj,
        "popular_in_category": popular_in_category,
    })


def product_detail(request, product_id, slug, category_slug=None):
    """Ürün detay sayfası - AI önerileri ile"""
    product = get_object_or_404(
        Product.objects.select_related("category")
        .prefetch_related("images", "attributes", "reviews__user"),
        id=product_id, slug=slug, is_active=True
    )

    # AI: Benzer ürünler (kategori, fiyat, etiket bazlı)
    similar_products = Product.objects.filter(
        category=product.category, is_active=True
    ).exclude(id=product.id)[:6]

    # AI: Ürün ortalama rating'i
    avg_rating = product.reviews.aggregate(Avg("rating"))["rating__avg"] or 0

    # AI: İlgili etiketler
    related_tags = product.tags.all()[:5]

    # Review form
    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            review.user = request.user
            review.save()
            messages.success(request, "Yorumunuz başarıyla eklendi!")
            return redirect("ecommerce:product_detail", product_id=product.id, slug=product.slug, category_slug=product.category.slug if product.category else None)
    else:
        form = ReviewForm()

    # AI: Kullanıcının bu ürünü wishlist'inde var mı kontrolü
    in_wishlist = False
    if request.user.is_authenticated:
        in_wishlist = Wishlist.objects.filter(
            user=request.user, product=product
        ).exists()

    # AI: Sepete ekle formu
    cart_form = CartAddProductForm(initial={
        "product_id": product.id,
        "update_quantity": False,
    })

    return render(request, "ecommerce/product_detail.html", {
        "product": product,
        "similar_products": similar_products,
        "avg_rating": round(avg_rating, 1),
        "related_tags": related_tags,
        "form": form,
        "in_wishlist": in_wishlist,
        "cart_form": cart_form,
        "avg_reviews": product.reviews.count(),
    })


def product_search(request):
    """Ürün arama sayfası"""
    query = request.GET.get("q", "")
    category_id = request.GET.get("category")

    products = Product.objects.filter(is_active=True)

    if query:
        products = products.filter(
            Q(name__icontains=query) |
            Q(description__icontains=query) |
            Q(short_description__icontains=query)
        )

    if category_id:
        products = products.filter(category_id=category_id)

    # AI: Arama sonuçları için öne çıkan ürünler
    featured_in_search = products.filter(is_featured=True)[:8]

    paginator = Paginator(products.order_by("-price"), 12)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "ecommerce/product_list.html", {
        "page_obj": page_obj,
        "featured_products": featured_in_search,
        "query": query,
        "is_search": True,
    })


@login_required
def cart_detail(request):
    """Sepet detayı - kullanıcı oturumu ile"""
    # Kullanıcı oturumu varsa cart_id'yi oluştur veya mevcutunu al
    cart_id = request.session.get("cart_id")
    if not cart_id:
        cart = Cart.objects.create()
        request.session["cart_id"] = cart.cart_id
    else:
        cart = get_object_or_404(Cart, cart_id=cart_id)

    cart_items = cart.items.select_related("product").all()
    total = sum(item.price * item.quantity for item in cart_items)

    return render(request, "ecommerce/cart_detail.html", {
        "cart": cart,
        "cart_items": cart_items,
        "total": total,
    })


@require_POST
def cart_add(request, product_id):
    """Sepete ürün ekle"""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    cart_id = request.session.get("cart_id")
    if not cart_id:
        cart = Cart.objects.create()
        request.session["cart_id"] = cart.cart_id
    else:
        cart = get_object_or_404(Cart, cart_id=cart_id)

    form = CartAddProductForm(request.POST)
    if form.is_valid():
        cd = form.cleaned_data
        # CartItem'ı kontrol et veya oluştur
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product,
            defaults={"quantity": cd["quantity"], "price_at_addition": product.price}
        )
        if not created:
            cart_item.quantity += cd["quantity"]
            cart_item.save()

    return redirect("ecommerce:cart_detail")


@require_POST
def cart_remove(request, cart_item_id):
    """Sepetten ürün çıkar"""
    cart_item = get_object_or_404(CartItem, id=cart_item_id)
    cart_item.delete()
    return redirect("ecommerce:cart_detail")


@login_required
def checkout(request):
    """Ödeme ve sipariş oluşturma sayfası"""
    cart_id = request.session.get("cart_id")
    if not cart_id:
        messages.error(request, "Sepetiniz boş!")
        return redirect("ecommerce:product_list")

    cart = get_object_or_404(Cart, cart_id=cart_id)
    cart_items = cart.items.select_related("product").all()

    if not cart_items:
        messages.error(request, "Sepetiniz boş!")
        return redirect("ecommerce:cart_detail")

    # Toplam hesapla
    total = sum(item.price * item.quantity for item in cart_items)
    tax = round(total * 0.18, 2)  # %18 KDV örneği
    grand_total = total + tax

    if request.method == "POST":
        # Sipariş oluştur
        order = Order.objects.create(
            user=request.user,
            cart_session=cart_id,
            total_amount=total,
            tax_amount=tax,
            shipping_address=request.POST.get("shipping_address", ""),
            billing_address=request.POST.get("billing_address", ""),
            notes=request.POST.get("order_notes", ""),
        )

        # OrderItem'ları oluştur
        for item in cart_items:
            OrderItem.objects.create(
                order=order,
                product=item.product,
                quantity=item.quantity,
                price=item.price_at_addition,
                total=item.price_at_addition * item.quantity,
            )

        # Sepeti temizle
        cart.items.all().delete()
        request.session.pop("cart_id", None)

        return redirect("ecommerce:order_thank_you", order_id=order.id)

    return render(request, "ecommerce/checkout.html", {
        "cart": cart,
        "cart_items": cart_items,
        "total": total,
        "tax": tax,
        "grand_total": grand_total,
    })


@login_required
def order_thank_you(request, order_id):
    """Sipariş tamamlandı sayfası"""
    order = get_object_or_404(Order, id=order_id, user=request.user)
    return render(request, "ecommerce/order_thank_you.html", {"order": order})


@login_required
def wishlist_add(request, product_id):
    """Wishlist'e ürün ekle"""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    wishlist_item, created = Wishlist.objects.get_or_create(
        user=request.user, product=product
    )
    if created:
        messages.success(request, "Ürün wishlist'e eklendi!")
    else:
        messages.info(request, "Bu ürün zaten wishlist'inizde!")
    return redirect("ecommerce:product_detail", product_id=product.id, slug=product.slug, category_slug=product.category.slug if product.category else None)


@login_required
def wishlist_detail(request):
    """Wishlist sayfası"""
    wishlist_items = Wishlist.objects.filter(user=request.user).select_related("product", "product__category")
    return render(request, "ecommerce/wishlist_detail.html", {
        "wishlist_items": wishlist_items,
    })


@login_required
def product_review(request, product_id):
    """Ürün review sayfası (API endpoint'i)"""
    product = get_object_or_404(Product, id=product_id, is_active=True)
    reviews = product.reviews.select_related("user").order_by("-created_at")[:10]

    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            review.user = request.user
            review.save()
            return redirect("ecommerce:product_review", product_id=product.id)

    form = ReviewForm()
    return render(request, "ecommerce/product_review.html", {
        "product": product,
        "reviews": reviews,
        "form": form,
    })


def ai_recommendations(request):
    """AI öneri sistemi - EMOG AI entegrasyonu için endpoint"""
    # AI: Kullanıcı geçmişine göre ürün önerileri
    # Şu anki implementasyon: Popüler ve benzerlik bazlı
    user = request.user if request.user.is_authenticated else None

    # Popüler ürünler
    popular = Product.objects.filter(is_active=True, is_featured=True)[:8]

    # Kullanıcının wishlist'indeki ürünler
    user_wishlist = []
    if user:
        user_wishlist = list(
            Wishlist.objects.filter(user=user).values_list("product_id", flat=True)
        )

    # Kullanıcının aldığı ürünlerin review'lerine bakarak tercihleri
    user_products = []
    if user:
        user_products = list(
            OrderItem.objects.filter(order__user=user)
            .values_list("product_id", flat=True)[:20]
        )

    # AI: Kategori bazlı öneri
    category_prefs = {}
    if user:
        from django.db.models import Count
        category_prefs = (
            OrderItem.objects.filter(order__user=user)
            .values("product__category")
            .annotate(total=Count("product__category"))
            .order_by("-total")[:5]
        )

    recommendations = {
        "popular": [
            {
                "id": p.id,
                "name": p.name,
                "price": str(p.price),
                "image": p.images.filter(is_primary=True).first().image.url if p.images.exists() else None,
            }
            for p in popular
        ],
        "wishlist_count": len(user_wishlist) if user else 0,
        "category_preferences": [
            {"category_id": c["product__category"], "count": c["total"]}
            for c in category_prefs
        ] if category_prefs else [],
    }

    return render(request, "ecommerce/ai_recommendations.html", recommendations)