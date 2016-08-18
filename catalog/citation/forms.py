from django import forms
from django.contrib.auth.forms import AuthenticationForm
# from django.utils.translation import ugettext_lazy as _

from .models import Publication

from haystack.forms import SearchForm

import logging

logger = logging.getLogger(__name__)


class CatalogAuthenticationForm(AuthenticationForm):

    username = forms.CharField(max_length=254, widget=forms.TextInput(attrs={'autofocus': True}))


class CatalogSearchForm(SearchForm):

    STATUS_CHOICES = [("", "Any")] + Publication.Status

    publication_start_date = forms.DateField(required=False)
    publication_end_date = forms.DateField(required=False)
    contact_email = forms.BooleanField(required=False)
    status = forms.ChoiceField(choices=STATUS_CHOICES, required=False)
    assigned_curator = forms.CharField(required=False)

    def no_query_found(self):
        return self.searchqueryset.models(Publication).all()

    def search(self):
        # First, store the SearchQuerySet received from other processing.
        # NOTE: Keep on adding the publication subtypes to models below to show them in search
        if not self.is_valid():
            return self.no_query_found()

        sqs = super(CatalogSearchForm, self).search()
        logger.debug("searching on %s", self.cleaned_data)

        criteria = {}
        # Check to see if a start_date was chosen.
        if self.cleaned_data['publication_start_date']:
            criteria.update(date_published__gte=self.cleaned_data['publication_start_date'])

        # Check to see if an end_date was chosen.
        if self.cleaned_data['publication_end_date']:
            criteria.update(date_published__lte=self.cleaned_data['publication_end_date'])

        # Check to see if status was selected.
        if self.cleaned_data['status']:
            criteria.update(status=self.cleaned_data['status'])

        # Check to see if assigned_curator was selected.
        if self.cleaned_data['assigned_curator']:
            criteria.update(assigned_curator=self.cleaned_data['assigned_curator'])

        sqs = sqs.filter(**criteria)

        if self.cleaned_data['contact_email']:
            sqs = sqs.exclude(contact_email__exact='')

        # if not using query to search, return the results sorted by date
        if not self.cleaned_data['q']:
            sqs = sqs.order_by('-date_published')
        return sqs