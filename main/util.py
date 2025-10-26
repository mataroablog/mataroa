import io
import re
import uuid
import zipfile

import bleach
import markdown
import pygments
from bleach.css_sanitizer import CSSSanitizer
from django.conf import settings
from django.utils.text import slugify
from pygments.formatters import HtmlFormatter
from pygments.lexers import ClassNotFound, get_lexer_by_name, get_lexer_for_filename
from l2m4m import LaTeX2MathMLExtension

from main import denylist, models


def is_disallowed(username):
    """Return true if username is not allowed to be registered."""
    if username[0] == "_":
        # do not allow leading underscores
        return True
    return username in denylist.DISALLOWED_USERNAMES


def get_approx_number(number):
    """Get approximate number, eg. 1823 -> 2k"""
    if number > 999:
        approx = round(number / 1000)
        return f"{approx}k"

    return number


def create_post_slug(post_title, owner, post=None):
    """
    Generate slug given post title. Optional post arg for post that already
    exists.
    """
    slug = slugify(post_title)

    # in case of post_title such as این متن است
    if not slug:
        generated_uuid = str(uuid.uuid4())[:8]
        slug = f"{generated_uuid[:3]}-{generated_uuid[3:5]}-{generated_uuid[5:]}"

    # if post is not None, then this is an update op
    if post is not None:
        post_with_same_slugs = models.Post.objects.filter(owner=owner, slug=slug)
        if post_with_same_slugs:
            if post_with_same_slugs.first().id == post.id:
                # if post being updating is the same one, then just return the same slug
                return slug
            else:
                # if post being updating is another one, then add a suffix to differentiate
                slug += "-" + str(uuid.uuid4())[:8]
                return slug

    # if post arg is None, then this is a new post
    # if slug already exists for another post, add a suffix to make it unique
    if models.Post.objects.filter(owner=owner, slug=slug).exists():
        slug += "-" + str(uuid.uuid4())[:8]

    return slug


def syntax_highlight(text):
    """Highlights markdown codeblocks within a markdown text."""

    processed_text = ""
    within_code_block = False
    lexer = None
    code_block = ""
    for line in text.split("\n"):
        # code block backticks found, either begin or end
        if line[:3] == "```":
            if not within_code_block:
                # then this is the beginning of a block
                lang = line[3:].strip()

                if lang:
                    # then this is a *code* block
                    within_code_block = True
                    lang_filename = "file." + lang
                    try:
                        lexer = get_lexer_for_filename(lang_filename)
                    except ClassNotFound:
                        try:
                            lexer = get_lexer_by_name(lang)
                        except ClassNotFound:
                            # can't find lexer, just use C lang as default
                            lexer = get_lexer_by_name("c")

                    # continue because we don't want to add backticks in the processed text
                    continue
                else:
                    # no lang, so just a generic block (non-code)
                    lexer = None

            else:
                # then this is the end of a code block
                # actual highlighting happens here
                within_code_block = False
                highlighted_block = pygments.highlight(
                    code_block,
                    lexer,
                    HtmlFormatter(style="solarized-light", noclasses=True, cssclass=""),
                )
                processed_text += highlighted_block
                code_block = ""  # reset code_block variable

                # continue because we don't want to add backticks in the processed text
                continue

        if within_code_block:
            code_block += line + "\n"
        else:
            processed_text += line + "\n"

    return processed_text


def clean_html(dirty_html, strip_tags=False):
    """Clean potentially evil HTML.

    - strip_tags: true will strip everything, false will escape.
    """
    if strip_tags:
        return bleach.clean(dirty_html, strip=True)

    css_sanitizer = CSSSanitizer(allowed_css_properties=denylist.ALLOWED_CSS_STYLES)
    return bleach.clean(
        dirty_html,
        tags=denylist.ALLOWED_HTML_ELEMENTS,
        attributes=denylist.ALLOWED_HTML_ATTRS,
        css_sanitizer=css_sanitizer,
    )


def md_to_html(markdown_string, strip_tags=False):
    """Return HTML formatted string, given a markdown one."""
    if not markdown_string:
        return ""
    dirty_html = markdown.markdown(
        syntax_highlight(markdown_string),
        extensions=[
            "markdown.extensions.fenced_code",
            "markdown.extensions.tables",
            "markdown.extensions.footnotes",
            "markdown.extensions.toc",
            LaTeX2MathMLExtension(),
        ],
    )
    return clean_html(dirty_html, strip_tags)


def remove_control_chars(text):
    """Remove control characters from a string.

    We remove all characters of the Cc category of unicode, except for
    \t (tab), \n (new line), \r (carriage return).
    See http://www.unicode.org/reports/tr44/#General_Category_Values
    """
    control_char_string = "".join(denylist.DISALLOWED_CHARACTERS)
    control_char_re = re.compile(f"[{re.escape(control_char_string)}]")
    return control_char_re.sub(" ", text)


def get_protocol():
    if settings.DEBUG:
        return "http:"
    else:
        return "https:"


def generate_markdown_export(user_id):
    """
    Generates a markdown export ZIP file in /tmp/.
    Returns (export name, export filepath).
    """
    # compile all posts into dictionary
    user = models.User.objects.get(id=user_id)
    user_posts = models.Post.objects.filter(owner=user)
    export_posts = []
    for p in user_posts:
        pub_date = p.published_at or p.created_at
        title = p.slug + ".md"
        body = f"# {p.title}\n\n"
        body += f"> Published on {pub_date.strftime('%b %-d, %Y')}\n\n"
        body += f"{p.body}\n"
        export_posts.append((title, io.BytesIO(body.encode())))

    # write zip archive in /tmp/
    export_name = "export-markdown-" + str(uuid.uuid4())[:8]
    container_dir = f"{user.username}-mataroa-blog"
    zip_outfile = f"/tmp/{export_name}.zip"
    with zipfile.ZipFile(
        zip_outfile, "a", zipfile.ZIP_DEFLATED, False
    ) as export_archive:
        for file_name, data in export_posts:
            export_archive.writestr(
                export_name + f"/{container_dir}/" + file_name, data.getvalue()
            )

    return (export_name, zip_outfile)


def escape_quotes(input_string):
    output_string = input_string.replace('"', '\\"')
    return output_string
