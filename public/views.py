from django.shortcuts import render


def home(request):
    """Scaffolding placeholder. Jurisdiction browse/search replaces this
    once registry models exist -- see docs/schema-spec.md, Milestone 1."""
    return render(request, "public/home.html")
