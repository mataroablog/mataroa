from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm as DjPasswordResetForm
from django.contrib.auth.forms import UserCreationForm as DjUserCreationForm
from django.core import validators as dj_validators

from main import models


class OnboardForm(forms.ModelForm):
    class Meta:
        model = models.Onboard
        fields = []


class UserCreationForm(DjUserCreationForm):
    class Meta:
        model = get_user_model()
        fields = ["username", "email"]


class PasswordResetForm(DjPasswordResetForm):
    def save(self, **kwargs):
        # Do not derive reset-link hosts from a blog or a supplied Host header.
        kwargs["domain_override"] = settings.CANONICAL_HOST
        return super().save(**kwargs)


class NotificationForm(forms.ModelForm):
    class Meta:
        model = models.Notification
        fields = ["email"]


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, list | tuple):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result


class UploadTextFilesForm(forms.Form):
    file = MultipleFileField()


class UploadImagesForm(forms.Form):
    file = MultipleFileField(
        validators=[
            dj_validators.FileExtensionValidator(
                ["jpeg", "jpg", "png", "svg", "gif", "webp", "tiff", "tif", "bmp"]
            )
        ],
    )


class StripeForm(forms.Form):
    card_token = forms.CharField(max_length=100, widget=forms.HiddenInput())


class ResetAPIKeyForm(forms.Form):
    """Reset user's api_key field."""


class APIDateField(forms.DateField):
    """Validate JSON date values before Django parses them."""

    def to_python(self, value):
        if value is not None and not isinstance(value, str):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        return super().to_python(value)


class APIPost(forms.Form):
    """Form for Post resource when accessed from the API."""

    title = forms.CharField(max_length=300, required=False)
    slug = forms.SlugField(max_length=300, required=False)
    body = forms.CharField(widget=forms.Textarea, required=False)
    published_at = APIDateField(required=False)


class APIPage(forms.Form):
    """Form for Page resource when accessed from the API."""

    title = forms.CharField(max_length=300, required=False)
    slug = forms.SlugField(max_length=300, required=False)
    body = forms.CharField(widget=forms.Textarea, required=False)
    is_hidden = forms.BooleanField(required=False)


class ModerationPostFilterForm(forms.Form):
    q = forms.CharField(label="Search posts", max_length=200, required=False)
    start = forms.DateField(
        label="Created from",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        label="Created through",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=[
            ("", "All posts"),
            ("published", "Published"),
            ("draft", "Drafts"),
            ("scheduled", "Scheduled"),
        ],
    )
    view = forms.ChoiceField(
        required=False,
        choices=[
            ("sample", "12 samples across history"),
            ("newest", "Browse newest first"),
            ("oldest", "Browse oldest first"),
            ("edited", "Browse recently edited"),
        ],
    )

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("start")
            and cleaned.get("end")
            and cleaned["start"] > cleaned["end"]
        ):
            raise forms.ValidationError(
                "The start date must be on or before the end date."
            )
        return cleaned
