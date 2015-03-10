from django.conf import settings
from django.contrib.auth.models import User
from django.core import signing
from django.core.mail import send_mass_mail, send_mail
from django.db.models import F

from rest_framework import serializers, pagination
from .models import Tag, Sponsor, Platform, Creator, Publication, JournalArticle, InvitationEmail

from hashlib import sha1

import time


class UserSerializer(serializers.ModelSerializer):
    """
    Serializes user querysets.
    """
    publications = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = User
        fields = ('id', 'username', 'publications')


class PublicationSerializer(serializers.ModelSerializer):
    """
    Serializes publication querysets.
    """
    class Meta:
        model = Publication
        fields = ('id', 'title', 'date_published')


class PaginatedPublicationSerializer(pagination.PaginationSerializer):
    """
    Serializes page objects of user querysets.
    """
    start_index = serializers.SerializerMethodField()
    end_index = serializers.SerializerMethodField()
    num_pages = serializers.ReadOnlyField(source='paginator.num_pages')
    current_page = serializers.SerializerMethodField()

    class Meta:
        object_serializer_class = PublicationSerializer

    def get_start_index(self, page):
        return page.start_index()

    def get_end_index(self, page):
        return page.end_index()

    def get_current_page(self, page):
        return page.number


class ContactUsSerializer(serializers.Serializer):
    name = serializers.CharField()
    email = serializers.EmailField()
    message = serializers.CharField()

    security_hash = serializers.CharField()
    timestamp = serializers.CharField()
    # honeypot field
    contact_number = serializers.CharField(allow_blank=True)

    def validate_contact_number(self, value):
        if value:
            raise serializers.ValidationError("Bot Alert...")
        return value

    def validate_timestamp(self, value):
        difference = float(time.time()) - float(value)
        if difference > (2 * 60 * 60) or difference < 5:
            raise serializers.ValidationError("Timestamp check failed")
        return value

    def validate(self, data):
        security_hash = data['security_hash']
        timestamp = str(data['timestamp'])

        info = (timestamp, settings.SECRET_KEY)
        new_security_hash = sha1("".join(info)).hexdigest()
        if security_hash != new_security_hash:
            raise serializers.ValidationError("timestamp was tampered!!!")
        return data

    def save(self):
        name = self.validated_data['name']
        email = self.validated_data['email']
        message = self.validated_data['message']

        send_mail(from_email=email, message=message, subject="Some Subject", recipient_list=[settings.DEFAULT_FROM_EMAIL])


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag


class PlatformSerializer(serializers.ModelSerializer):
    class Meta:
        model = Platform


class CreatorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Creator


class SponsorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sponsor


class JournalArticleSerializer(serializers.ModelSerializer):
    """
    Serializes journal article querysets
    """
    tags = TagSerializer(many=True, read_only=True)
    platforms = PlatformSerializer(many=True, read_only=True)
    creators = CreatorSerializer(many=True, read_only=True)
    sponsors = SponsorSerializer(many=True, read_only=True)

    class Meta:
        model = JournalArticle


class InvitationSerializer(serializers.Serializer):
    invitation_subject = serializers.CharField()
    invitation_text = serializers.CharField()

    def save(self, request, pk_list):
        subject = self.validated_data['invitation_subject']
        message = self.validated_data['invitation_text']

        pub_list = Publication.objects.filter(pk__in=pk_list).exclude(contact_email__exact='')
        messages = []

        for pub in pub_list:
            token = signing.dumps(pub.pk, salt=settings.SALT)
            ie = InvitationEmail(request)
            body = ie.get_plaintext_content(message, token)
            messages.append((subject, body, settings.DEFAULT_FROM_EMAIL, [pub.contact_email]))
        send_mass_mail(messages, fail_silently=False)
        pub_list.update(email_sent_count=F('email_sent_count') + 1)


class ArchivePublicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Publication
        fields = ('title', 'code_archive_url', 'author_comments')
