from django.test import SimpleTestCase

from main import denylist, text_processing


class RemoveControlCharsTestCase(SimpleTestCase):
    def test_replaces_disallowed_control_chars_with_spaces(self):
        text = "".join(denylist.DISALLOWED_CHARACTERS)

        self.assertEqual(
            text_processing.remove_control_chars(text),
            " " * len(denylist.DISALLOWED_CHARACTERS),
        )

    def test_preserves_allowed_control_chars(self):
        text = "tab:\t newline:\n carriage-return:\r"

        self.assertEqual(text_processing.remove_control_chars(text), text)


class RemoveSurrogateCharsTestCase(SimpleTestCase):
    def test_removes_high_and_low_surrogate_chars(self):
        text = "before\ud835middle\udc00after"

        self.assertEqual(
            text_processing.remove_surrogate_chars(text),
            "beforemiddleafter",
        )

    def test_preserves_valid_non_bmp_chars(self):
        text = "Mathematical A: \U0001d400; emoji: \U0001f600"

        self.assertEqual(text_processing.remove_surrogate_chars(text), text)
