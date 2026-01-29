from django.contrib.auth import get_user_model
from django.forms import BooleanField, ValidationError
from django.utils.timezone import now
from django.utils.translation import gettext as _
from modelcluster.forms import BaseChildFormSet
from modelcluster.models import get_serializable_data_for_fields

from wagtail.admin.templatetags.wagtailadmin_tags import avatar_url, user_display_name
from wagtail.models import COMMENTS_RELATION_NAME

from .models import WagtailAdminModelForm


class CommentReplyForm(WagtailAdminModelForm):
    class Meta:
        fields = ("text",)

    def clean(self):
        cleaned_data = super().clean()
        user = self.for_user

        if not self.instance.pk:
            self.instance.user = user
        elif self.instance.user != user:
            # trying to edit someone else's comment reply
            if any(field for field in self.changed_data):
                # includes DELETION_FIELD_NAME, as users cannot delete each other's individual comment replies
                # if deleting a whole thread, this should be done by deleting the parent Comment instead
                self.add_error(
                    None, ValidationError(_("You cannot edit another user's comment."))
                )
        return cleaned_data

    def serialize(self, bound):
        data = get_serializable_data_for_fields(self.instance)
        data["deleted"] = self.cleaned_data.get("DELETE", False) if bound else False
        return data, {self.instance.user_id}


class CommentForm(WagtailAdminModelForm):
    """
    This is designed to be subclassed and have the user overridden to enable user-based validation within the edit handler system
    """

    resolved = BooleanField(required=False)

    class Meta:
        formsets = {
            "replies": {
                "form": CommentReplyForm,
                "inherit_kwargs": ["for_user"],
            }
        }

    def clean(self):
        cleaned_data = super().clean()
        user = self.for_user

        if not self.instance.pk:
            self.instance.user = user
        elif self.instance.user != user:
            # trying to edit someone else's comment
            if (
                any(
                    field
                    for field in self.changed_data
                    if field not in ["resolved", "position", "contentpath"]
                )
                or cleaned_data["contentpath"].split(".")[0]
                != self.instance.contentpath.split(".")[0]
            ):
                # users can resolve each other's base comments and change their positions within a field, or move a comment between blocks in a StreamField
                self.add_error(
                    None, ValidationError(_("You cannot edit another user's comment."))
                )
        return cleaned_data

    def save(self, *args, **kwargs):
        if self.cleaned_data.get("resolved", False):
            if not self.instance.resolved_at:
                self.instance.resolved_at = now()
                self.instance.resolved_by = self.for_user
        else:
            self.instance.resolved_by = None
            self.instance.resolved_at = None
        return super().save(*args, **kwargs)

    def serialize(self, bound):
        user_pks = {self.instance.user_id}
        replies = []
        for reply_form in self.formsets["replies"].forms:
            reply_data, reply_user_pks = reply_form.serialize(bound)
            replies.append(reply_data)
            user_pks.update(reply_user_pks)

        data = get_serializable_data_for_fields(self.instance)
        data["deleted"] = self.cleaned_data.get("DELETE", False) if bound else False
        data["resolved"] = (
            self.cleaned_data.get("resolved", False)
            if bound
            else self.instance.resolved_at is not None
        )
        data["replies"] = replies
        return data, user_pks


class CommentFormSet(BaseChildFormSet):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # We used to filter the queryset here, but that hides orphans from the cleanup logic in save().
        # We now do the filtering/cleanup in save() and serialize().

    def save(self, commit=True):
        # At this point, self.instance has been updated with the new form data
        instances = super().save(commit=commit)

        # Identify orphaned comments (blocks that have been removed)
        # We check the entire set of comments associated with the page
        comments_rel = getattr(self.instance, COMMENTS_RELATION_NAME)
        orphans = [
            comment for comment in comments_rel.all()
            if not comment.has_valid_contentpath(self.instance)
        ]

        for comment in orphans:
            comments_rel.remove(comment)
            comment.delete()

        # Delete comments where the position has been cleared in a Rich Text field
        for form in self.forms:
            if (
                form.instance.pk
                and form.cleaned_data.get("position") == "[]"
                and not form.cleaned_data.get("DELETE")
            ):
                # removing from the parent's collection so it doesn't get re-saved or included in revisions
                comments_rel = getattr(self.instance, COMMENTS_RELATION_NAME)
                comments_rel.remove(form.instance)
                form.instance.delete()
                if form.instance in instances:
                    instances.remove(form.instance)
        return instances

    def serialize(self, bound: bool, user):
        def user_data(user):
            return {"name": user_display_name(user), "avatar_url": avatar_url(user)}

        user_pks = {user.pk}
        serialized_comments = []
        for form in self.forms:
            # Skip orphaned comments in serialization to hide them from the UI
            is_orphan = not form.instance.has_valid_contentpath(self.instance)
            if bound:
                try:
                    is_orphan = is_orphan or form.cleaned_data.get("position") == "[]"
                except (AttributeError, KeyError):
                    # cleaned_data might not be available if form is invalid or not yet cleaned
                    pass
            elif form.instance.position == "[]":
                is_orphan = True

            if is_orphan:
                continue
            # iterate over comments to retrieve users (to get display names) and serialized versions
            data, comment_user_pks = form.serialize(bound)
            serialized_comments.append(data)
            user_pks.update(comment_user_pks)

        authors = {
            str(user.pk): user_data(user)
            for user in get_user_model()
            .objects.filter(pk__in=user_pks)
            .select_related("wagtail_userprofile")
        }

        comments_data = {
            "comments": serialized_comments,
            "user": user.pk,
            "authors": authors,
        }
        return comments_data
