from collections import Counter
from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME, login as auth_login
from django.contrib.auth.mixins import LoginRequiredMixin
# from django.contrib.auth.views import LoginView as DjangoLoginView
from django.core import signing
from django.db.models import Count
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, resolve_url
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.http import is_safe_url
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters

from django.views.generic import TemplateView, FormView, RedirectView
from haystack.query import SearchQuerySet
from haystack.generic_views import SearchView
from hashlib import sha1
from rest_framework.response import Response
from rest_framework import status, renderers, generics
from json import dumps
from datetime import timedelta, datetime
from django.core.urlresolvers import reverse

from .forms import CatalogSearchForm, CatalogAuthenticationForm
from .models import (Publication, InvitationEmail, Platform, Sponsor, Tag, Container, ModelDocumentation, Note, )
from .permissions import CanViewReadOnlyOrEditPublication
from .serializers import (PublicationSerializer, CatalogPagination, InvitationSerializer,
                          UpdateModelUrlSerializer, ContactFormSerializer, UserProfileSerializer, NoteSerializer,
                          ModelDocumentationSerializer)

import logging
import markdown
import time

logger = logging.getLogger(__name__)


class LoginView(FormView):
    """
    Pulled from github django master, replace when django.contrib.auth.views.LoginView is available in release.
    """
    form_class = CatalogAuthenticationForm
    template_name = 'accounts/login.html'
    authentication_form = None
    redirect_field_name = REDIRECT_FIELD_NAME
    redirect_authenticated_user = False
    extra_context = None

    @method_decorator(sensitive_post_parameters())
    @method_decorator(csrf_protect)
    @method_decorator(never_cache)
    def dispatch(self, request, *args, **kwargs):
        if self.redirect_authenticated_user and self.request.user.is_authenticated:
            redirect_to = self.get_success_url()
            if redirect_to == self.request.path:
                raise ValueError(
                    "Redirection loop for authenticated user detected. Check that "
                    "your LOGIN_REDIRECT_URL doesn't point to a login page."
                )
            return HttpResponseRedirect(redirect_to)
        return super(LoginView, self).dispatch(request, *args, **kwargs)

    def get_success_url(self):
        """Ensure the user-originating redirection URL is safe."""
        redirect_to = self.request.POST.get(
            self.redirect_field_name,
            self.request.GET.get(self.redirect_field_name, '')
        )
        url_is_safe = is_safe_url(
            url=redirect_to,
            host=self.request.get_host(),
        )
        if not url_is_safe:
            return resolve_url(settings.LOGIN_REDIRECT_URL)
        return redirect_to

    def get_form_class(self):
        return self.authentication_form or self.form_class

    def form_valid(self, form):
        """Security check complete. Log the user in."""
        auth_login(self.request, form.get_user())
        return HttpResponseRedirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        context = super(LoginView, self).get_context_data(**kwargs)
        context.update({
            self.redirect_field_name: self.get_success_url(),
        })
        if self.extra_context is not None:
            context.update(self.extra_context)
        return context


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, number_of_publications=25, **kwargs):
        context = super(DashboardView, self).get_context_data(**kwargs)
        last_week_datetime = timezone.now() - timedelta(days=7)
        context['status'] = Counter()

        pub_count = Publication.objects.filter(is_primary=True).values('status').annotate(total=Count('status')).order_by('-total')
        for item in pub_count:
            total_count = item['total']
            context['status'][item['status']] = total_count
            context['status']['TOTAL'] += total_count
        n_flagged = Publication.objects.filter(is_primary=True, flagged=True).count()
        context['flagged'] = n_flagged

        context['complete'] = Publication.objects.filter(
            is_primary=True, status=Publication.Status.REVIEWED).exclude(code_archive_url='').count()
        context['incomplete'] = context['status'][Publication.Status.REVIEWED] - context['complete']

        context['unreviewed_publications_count'] = Publication.objects.filter(
            status=Publication.Status.UNREVIEWED, assigned_curator=self.request.user).count()
        context['recently_author_updated'] = Publication.objects.filter(
            status=Publication.Status.AUTHOR_UPDATED)
        recently_updated_publications = Publication.objects.exclude(
            status=Publication.Status.AUTHOR_UPDATED)
        context['recently_updated'] = recently_updated_publications.filter(
            date_modified__gte=last_week_datetime, is_primary=True).order_by('-date_modified')[:number_of_publications]
        return context


class UserProfileView(LoginRequiredMixin, generics.GenericAPIView):
    """
    Retrieve or Update User Profile of current logged in User
    """
    renderer_classes = (renderers.TemplateHTMLRenderer, renderers.JSONRenderer)

    def get(self, request):
        serializer = UserProfileSerializer(instance=request.user)
        return Response({'profile': dumps(serializer.data)}, template_name="accounts/profile.html")

    def post(self, request):
        serializer = UserProfileSerializer(instance=request.user, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response({'profile': dumps(serializer.data)})
        return Response({'errors': dumps(serializer.errors)}, status=status.HTTP_400_BAD_REQUEST)


class PublicationList(LoginRequiredMixin, generics.GenericAPIView):
    """
    List all publications, or create a new publication
    """
    renderer_classes = (renderers.TemplateHTMLRenderer, renderers.JSONRenderer)
    # FIXME: look into properly implementing pagination via django rest framework
    pagination_class = CatalogPagination

    def get(self, request, format=None):
        publication_list = Publication.objects.all()
        paginator = CatalogPagination()
        result_page = paginator.paginate_queryset(publication_list, request)
        serializer = PublicationSerializer(result_page, many=True)
        response = paginator.get_paginated_response(serializer.data)
        return Response({'json': dumps(response)}, template_name="publication/list.html")

    def post(self, request, format=None):
        # adding current user to added_by field
        request.data.update({'added_by': request.user.id})
        # FIXME: hard coded PublicationSerializer should instead depend on incoming data
        serializer = PublicationSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CuratorPublicationDetail(LoginRequiredMixin, generics.GenericAPIView):
    """
    Retrieve, update or delete a publication instance.
    """
    renderer_classes = (renderers.TemplateHTMLRenderer, renderers.JSONRenderer)

    def get_object(self, pk):
        return get_object_or_404(Publication, pk=pk)

    def get(self, request, pk, slug, format=None):
        publication = self.get_object(pk)
        obj_url = publication.get_absolute_url()
        
        if self.request.path != obj_url:
            return HttpResponseRedirect(obj_url)
            
        serializer = PublicationSerializer(publication)
        model_documentation_serializer = ModelDocumentationSerializer(ModelDocumentation.objects.all(), many=True)
        return Response({'json': dumps(serializer.data), 'pk': pk,
                         'model_documentation_categories_json': dumps(ModelDocumentation.CATEGORIES),
                         'model_documentation_list_json': dumps(model_documentation_serializer.data)},
                        template_name='workflow/curator_publication_detail.html')

    def put(self, request, pk):
        publication = self.get_object(pk)
        # FIXME: need to revisit this if we ever have other Publication Types - Books or Book Chapters may also refer to
        # computational models.
        serializer = PublicationSerializer(publication, data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data)
        logger.warning("serializer failed validation: %s", serializer.errors)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class NoteDetail(LoginRequiredMixin, generics.GenericAPIView):
    """
    Retrieve, update or delete a note instance.
    """
    renderer_classes = (renderers.JSONRenderer,)

    def get_object(self, pk):
        return get_object_or_404(Note, pk=pk)

    def get(self, request, pk, format=None):
        note = self.get_object(pk)
        serializer = NoteSerializer(note)
        return Response({'json': dumps(serializer.data)})

    def put(self, request, pk):
        note = self.get_object(pk)
        serializer = NoteSerializer(note, data=request.data)
        logger.debug("serializer: %s", serializer)
        if serializer.is_valid():
            serializer.save(added_by=request.user)
            return Response(serializer.data)
        logger.error("serializer errors: %s", serializer.errors)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk, format=None):
        note = self.get_object(pk)
        note.deleted_by = request.user
        note.deleted_on = datetime.today()
        note.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NoteList(LoginRequiredMixin, generics.GenericAPIView):
    """
    Get all the notes or create a note
    """
    renderer_classes = (renderers.JSONRenderer,)
    serializer_class = NoteSerializer

    def get(self, request, format=None):
        note = Note.objects.all()
        serializer = NoteSerializer(note, many=True)
        return Response({'json': dumps(serializer.data)})

    def post(self, request):
        # adding current user to added_by field
        serializer = NoteSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(added_by=request.user)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class EmailPreview(LoginRequiredMixin, generics.GenericAPIView):
    """
    Preview the final email content
    """
    renderer_classes = (renderers.JSONRenderer,)

    def get(self, request, format=None):
        serializer = InvitationSerializer(data=request.GET)
        if serializer.is_valid():
            message = serializer.validated_data['invitation_text']
            ie = InvitationEmail(self.request)
            plaintext_content = ie.get_plaintext_content(message, "valid:token")
            html_content = markdown.markdown(plaintext_content)
            return Response({'content': html_content})
        return Response({'content': serializer.errors}, status=status.HTTP_400_BAD_REQUEST)


class AutocompleteView(LoginRequiredMixin, generics.GenericAPIView):
    renderer_classes = (renderers.JSONRenderer,)

    def get(self, request, format=None):
        query = request.GET.get('q', '').strip()
        sqs = SearchQuerySet().models(self.model_class)
        if query:
            sqs = sqs.autocomplete(name=query)
        data = [{'id': int(result.pk), 'name': result.name} for result in sqs]
        return Response(dumps(data))


class PlatformSearchView(AutocompleteView):
    @property
    def model_class(self):
        return Platform


class SponsorSearchView(AutocompleteView):
    @property
    def model_class(self):
        return Sponsor


class TagSearchView(AutocompleteView):
    @property
    def model_class(self):
        return Tag


class ModelDocumentationSearchView(AutocompleteView):
    @property
    def model_class(self):
        return ModelDocumentation


class JournalSearchView(AutocompleteView):
    @property
    def model_class(self):
        return Container


class CatalogSearchView(LoginRequiredMixin, SearchView):
    """ generic django haystack SearchView using a custom form """
    form_class = CatalogSearchForm


class CuratorWorkflowView(LoginRequiredMixin, SearchView):
    """ django haystack searchview """
    template_name = 'workflow/curator.html'
    form_class = CatalogSearchForm

    def get_context_data(self, **kwargs):
        context = super(CuratorWorkflowView, self).get_context_data(**kwargs)
        sqs = SearchQuerySet().filter(assigned_curator=self.request.user, is_primary=True).facet('status')
        context.update(facets=sqs.facet_counts(),
                       total_number_of_records=Publication.objects.filter(assigned_curator=self.request.user).count())
        return context

    def get_queryset(self):
        sqs = super(CuratorWorkflowView, self).get_queryset()
        return sqs.filter(assigned_curator=self.request.user, is_primary=True).order_by('-last_modified', '-status')


class ContactAuthor(LoginRequiredMixin, generics.GenericAPIView):
    """
    Emails invitations to authors to archive their work
    """
    renderer_classes = (renderers.JSONRenderer,)

    def post(self, request, format=None):
        serializer = InvitationSerializer(data=request.data)
        pk_list = CatalogSearchForm(request.GET or None).search().values_list('pk', flat=True)
        if serializer.is_valid():
            serializer.save(self.request, pk_list)
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ContactFormView(generics.GenericAPIView):
    renderer_classes = (renderers.TemplateHTMLRenderer, renderers.JSONRenderer)

    def get(self, request):
        timestamp = str(time.time())
        info = (timestamp, settings.SECRET_KEY)
        security_hash = sha1("".join(info).encode("ascii"), ).hexdigest()

        data = {'contact_number': '',
                'name': '',
                'timestamp': timestamp,
                'security_hash': security_hash,
                'message': '',
                'email': ''}
        user = request.user
        if user.is_authenticated():
            data.update(name=user.get_full_name(), email=user.email)
        serializer = ContactFormSerializer(data)
        return Response({'json': dumps(serializer.data)}, template_name='contact_us.html')

    def post(self, request):
        serializer = ContactFormSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return JsonResponse(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UpdateModelUrlView(generics.GenericAPIView):
    renderer_classes = (renderers.TemplateHTMLRenderer, renderers.JSONRenderer)
    token_expires = 3600 * 168  # Seven days

    def get_object(self, token):
        try:
            pk = signing.loads(token, max_age=self.token_expires, salt=settings.SALT)
        except signing.BadSignature:
            pk = None
        return get_object_or_404(Publication, pk=pk)

    def get(self, request, token, format=None):
        serializer = UpdateModelUrlSerializer(self.get_object(token))
        return Response({'json': dumps(serializer.data)}, template_name='publication/update_model_url.html')

    def post(self, request, token, format=None):
        serializer = UpdateModelUrlSerializer(self.get_object(token), data=request.data)
        if serializer.is_valid():
            serializer.validated_data['status'] = Publication.STATUS_CHOICES.AUTHOR_UPDATED
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
