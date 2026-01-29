from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from wagtail.models import Page, Comment
from wagtail.test.testapp.models import EventPage, StreamPage
from wagtail.test.utils import WagtailTestUtils
import json

class TestCommentOrphans(TestCase, WagtailTestUtils):
    def setUp(self):
        self.user = self.login()
        self.root_page = Page.objects.get(id=2)

    def test_delete_text_leaves_orphaned_comment(self):
        # We'll use StreamPage but with a comment on a field that exists
        stream_page = StreamPage(
            title="Stream",
            slug="stream",
            body=json.dumps([
                {'type': 'rich_text', 'value': '<p>Hello world</p>', 'id': 'block1'},
            ])
        )
        self.root_page.add_child(instance=stream_page)
        
        comment = Comment.objects.create(
            page=stream_page,
            user=self.user,
            text="Text comment",
            contentpath="body.block1",
            position=json.dumps([{"key": "some-key", "start": 0, "end": 5}])
        )

        # Scenario: User deletes the text containing the comment.
        # Draftail will send position="[]" for this comment.
        
        post_data = {
            'title': "Stream updated",
            'slug': "stream",
            'body-count': 1,
            'body-0-type': 'rich_text',
            'body-0-value': json.dumps({
                'blocks': [
                    {'key': 'new-key', 'text': 'Updated text', 'type': 'unstyled', 'depth': 0, 'inlineStyleRanges': [], 'entityRanges': [], 'data': {}}
                ],
                'entityMap': {}
            }),
            'body-0-id': 'block1',
            'body-0-order': 0,
            'body-0-deleted': '',
            'comments-TOTAL_FORMS': 1,
            'comments-INITIAL_FORMS': 1,
            'comments-MIN_NUM_FORMS': 0,
            'comments-MAX_NUM_FORMS': 1000,
            'comments-0-id': comment.id,
            'comments-0-text': "Text comment",
            'comments-0-contentpath': "body.block1",
            'comments-0-position': "[]", # Orphaned within block!
        }
        
        response = self.client.post(reverse('wagtailadmin_pages:edit', args=(stream_page.id,)), post_data)
        if response.status_code != 302:
             print(f"Context keys: {response.context.keys()}")
             self.fail(f"Status code 200 returned. Errors: {response.context['form'].errors}")
        self.assertEqual(response.status_code, 302)
        
        # Check if the comment still exists
        self.assertFalse(Comment.objects.filter(id=comment.id).exists(), "Comment should NOT exist after text deletion, but it does!")

    def test_delete_block_leaves_orphaned_comment(self):
        # Create a StreamPage with a comment in a block
        stream_page = StreamPage(
            title="Stream 2",
            slug="stream2",
            body=json.dumps([
                {'type': 'text', 'value': 'Block 1', 'id': 'block1'},
            ])
        )
        self.root_page.add_child(instance=stream_page)
        
        comment = Comment.objects.create(
            page=stream_page,
            user=self.user,
            text="Block comment",
            contentpath="body.block1",
            position=""
        )
        
        # Scenario: User deletes the block.
        # The form will NOT contain comments-0 data for this comment because it's filtered out of the formset by __init__
        
        post_data = {
            'title': "Stream updated",
            'slug': "stream2",
            'body-count': 0,
            'comments-TOTAL_FORMS': 0,
            'comments-INITIAL_FORMS': 1,
            'comments-MIN_NUM_FORMS': 0,
            'comments-MAX_NUM_FORMS': 1000,
        }
        
        response = self.client.post(reverse('wagtailadmin_pages:edit', args=(stream_page.id,)), post_data)
        self.assertEqual(response.status_code, 302)
        
        # Check if the comment still exists
        self.assertFalse(Comment.objects.filter(id=comment.id).exists(), "Comment should NOT exist after block deletion, but it does!")
