from django import forms
from .models import CartItem, Product, Review


class CartAddProductForm(forms.Form):
    """Sepete ürün ekle formu"""
    quantity = forms.IntegerField(
        label="Miktar", initial=1, min_value=1, max_value=20
    )
    update_quantity = forms.BooleanField(
        label="Miktarı güncelle", required=False, initial=False
    )


class ReviewForm(forms.ModelForm):
    """Yorum formu"""
    class Meta:
        model = Review
        fields = ["rating", "title", "comment"]
        widgets = {
            "rating": forms.Select(choices=[
                (1, "1 Yıldız"), (2, "2 Yıldız"), (3, "3 Yıldız"),
                (4, "4 Yıldız"), (5, "5 Yıldız")
            ]),
            "title": forms.TextInput(attrs={"placeholder": "Review başlığı"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "Yorumunuz..."}),
        }