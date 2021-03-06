import re

from django import template
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.forms import CheckboxInput
from django.conf import settings
from django.template.loader import get_template

from citation.models import Author, Platform, Sponsor, Tag, Container
from citation.util import render_sanitized_markdown

register = template.Library()


@register.filter(is_safe=True)
def markdown(markdown_text):
    return render_sanitized_markdown(markdown_text)


@register.filter(is_safe=True)
def active(request, pattern):
    return 'active' if pattern == request.path else ''


@register.filter(is_safe=True)
def active_re(request, pattern):
    return 'active' if re.search(pattern, request.path) else ''


@register.inclusion_tag('public/includes/pagination.html')
def pagination_control(paginator, current_page):
    return {'paginator': paginator, 'current_page': current_page}


@register.filter()
def list_authors(authors):
    if not authors:
        return 'Unknown'
    else:
        return ', '.join(a.name for a in authors)


@register.inclusion_tag('public/includes/explanation_tile.html')
def explanation_tile(title, description, position):
    return {'title': title, 'description': description, 'position': position}


@register.inclusion_tag('public/includes/facet_checkbox.html')
def facet_checkbox(instance, field):
    return {'name_attr': field,
            'id_attr': instance['id'],
            'checked': instance['checked'],
            'name': instance['name'],
            'statistic': instance['publication_count']}


@register.filter()
def add_field_css(field, css_classes: str):
    if field.errors:
        css_classes += ' is-invalid'
    css_classes = field.css_classes(css_classes)
    deduped_css_classes = ' '.join(set(css_classes.split(' ')))
    return field.as_widget(attrs={'class': deduped_css_classes})


@register.filter()
def is_checkbox(bound_field):
    return isinstance(bound_field.field.widget, CheckboxInput)


@register.inclusion_tag('public/includes/form.html')
def render_form(form):
    return {
        'form': form
    }


@register.inclusion_tag('public/includes/message.html')
def message(text, level):
    style = messages.DEFAULT_TAGS[level]
    return {'text': text, 'style': style}


@register.simple_tag()
def top_categories_by_content_type(content_type, matches):
    context_list_name = content_type
    if content_type == Author._meta.verbose_name_plural:
        template = get_template('public/includes/author_search_results.html')
    elif content_type == Container._meta.verbose_name_plural:
        template = get_template('public/includes/container_search_results.html')
    elif content_type in [ct._meta.verbose_name_plural for ct in [Platform, Sponsor, Tag]]:
        template = get_template('public/includes/related_search_results.html')
        context_list_name = 'matches'
    else:
        raise ValidationError('Content Type {} not allowed'.format(content_type))
    return template.render({context_list_name: matches})


@register.simple_tag()
def release_version():
    return settings.RELEASE_VERSION
