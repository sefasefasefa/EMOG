from django.shortcuts import render

def pricing_page(request):
    """Pricing page with Free/Pro/Team tiers."""
    return render(request, 'agent_app/pricing.html')