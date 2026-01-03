import io
import re
import uuid
import zipfile
from datetime import datetime
from string import Template

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from main import models, text_processing


def prepend_zola_frontmatter(body, post_title, pub_date):
    frontmatter = "+++\n"
    frontmatter += f'title = "{post_title}"\n'
    frontmatter += f"date = {pub_date}\n"
    frontmatter += 'template = "post.html"\n'
    frontmatter += "+++\n"
    frontmatter += "\n"

    return frontmatter + body


def prepend_hugo_frontmatter(body, post_title, pub_date, post_slug):
    frontmatter = "+++\n"
    frontmatter += f'title = "{post_title}"\n'
    frontmatter += f"date = {pub_date}\n"
    frontmatter += f'url = "blog/{post_slug}"\n'
    frontmatter += "+++\n"
    frontmatter += "\n"

    return frontmatter + body


def export_index(request):
    return render(request, "main/export_index.html")


@require_POST
@login_required
def export_markdown(request):
    # get all user posts and add them into export_posts encoded
    posts = models.Post.objects.filter(owner=request.user)
    export_posts = []
    for p in posts:
        pub_date = p.published_at or p.created_at
        title = p.slug + ".md"
        body = f"# {p.title}\n\n"
        body += f"> Published on {pub_date.strftime('%b %-d, %Y')}\n\n"
        body += f"{p.body}\n"
        export_posts.append((title, io.BytesIO(body.encode())))

    # create zip archive in memory
    export_name = "export-markdown-" + str(uuid.uuid4())[:8]
    container_dir = f"{request.user.username}-mataroa-blog"
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as export_archive:
        for file_name, data in export_posts:
            export_archive.writestr(
                export_name + f"/{container_dir}/" + file_name, data.getvalue()
            )

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f"attachment; filename={export_name}.zip"
    return response


@require_POST
@login_required
def export_zola(request):
    # load zola templates
    with open("./export_base_zola/config.toml") as zola_config_file:
        zola_config = (
            zola_config_file.read()
            .replace("example.com", f"{request.user.username}.mataroa.blog")
            .replace("Example blog title", f"{request.user.username} blog")
            .replace("Example blog description", f"{request.user.blog_byline or ''}")
        )
    with open("./export_base_zola/style.css") as zola_styles_file:
        zola_styles = zola_styles_file.read()
    with open("./export_base_zola/index.html") as zola_index_file:
        zola_index = zola_index_file.read()
    with open("./export_base_zola/post.html") as zola_post_file:
        zola_post = zola_post_file.read()
    with open("./export_base_zola/_index.md") as zola_content_index_file:
        zola_content_index = zola_content_index_file.read()
    with open("./export_base_zola/404.html") as zola_404_file:
        zola_404 = zola_404_file.read()

    # get all user posts and add them into export_posts encoded
    posts = models.Post.objects.filter(owner=request.user)
    export_posts = []
    for p in posts:
        pub_date = p.published_at or p.created_at.date()
        title = p.slug + ".md"
        body = prepend_zola_frontmatter(
            p.body, text_processing.escape_quotes(p.title), pub_date
        )
        export_posts.append((title, io.BytesIO(body.encode())))

    # create zip archive in memory
    export_name = "export-zola-" + str(uuid.uuid4())[:8]
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as export_archive:
        export_archive.writestr(export_name + "/config.toml", zola_config)
        export_archive.writestr(export_name + "/static/style.css", zola_styles)
        export_archive.writestr(export_name + "/templates/index.html", zola_index)
        export_archive.writestr(export_name + "/templates/post.html", zola_post)
        export_archive.writestr(export_name + "/templates/404.html", zola_404)
        export_archive.writestr(export_name + "/content/_index.md", zola_content_index)
        for file_name, data in export_posts:
            export_archive.writestr(
                export_name + "/content/" + file_name, data.getvalue()
            )

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f"attachment; filename={export_name}.zip"
    return response


@require_POST
@login_required
def export_hugo(request):
    # load hugo templates
    with open("./export_base_hugo/config.toml") as hugo_config_file:
        blog_title = request.user.blog_title or f"{request.user.username} blog"
        blog_byline = request.user.blog_byline or ""
        hugo_config = (
            hugo_config_file.read()
            .replace("example.com", f"{request.user.username}.mataroa.blog")
            .replace("Example blog title", blog_title)
            .replace("Example blog description", blog_byline)
        )
    with open("./export_base_hugo/theme.toml") as hugo_theme_file:
        hugo_theme = hugo_theme_file.read()
    with open("./export_base_hugo/style.css") as hugo_styles_file:
        hugo_styles = hugo_styles_file.read()
    with open("./export_base_hugo/single.html") as hugo_single_file:
        hugo_single = hugo_single_file.read()
    with open("./export_base_hugo/list.html") as hugo_list_file:
        hugo_list = hugo_list_file.read()
    with open("./export_base_hugo/index.html") as hugo_index_file:
        hugo_index = hugo_index_file.read()
    with open("./export_base_hugo/baseof.html") as hugo_baseof_file:
        hugo_baseof = hugo_baseof_file.read()
    with open("./export_base_hugo/404.html") as hugo_404_file:
        hugo_404 = hugo_404_file.read()

    # get all user posts and add them into export_posts encoded
    posts = models.Post.objects.filter(owner=request.user)
    export_posts = []
    for p in posts:
        title = p.slug + ".md"
        pub_date = p.published_at or p.created_at.date()
        body = prepend_hugo_frontmatter(
            p.body, text_processing.escape_quotes(p.title), pub_date, p.slug
        )
        export_posts.append((title, io.BytesIO(body.encode())))

    # create zip archive in memory
    export_name = "export-hugo-" + str(uuid.uuid4())[:8]
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as export_archive:
        export_archive.writestr(export_name + "/config.toml", hugo_config)
        export_archive.writestr(export_name + "/themes/mataroa/theme.toml", hugo_theme)
        export_archive.writestr(
            export_name + "/themes/mataroa/static/style.css", hugo_styles
        )
        export_archive.writestr(
            export_name + "/themes/mataroa/layouts/index.html", hugo_index
        )
        export_archive.writestr(
            export_name + "/themes/mataroa/layouts/404.html", hugo_404
        )
        export_archive.writestr(
            export_name + "/themes/mataroa/layouts/_default/single.html",
            hugo_single,
        )
        export_archive.writestr(
            export_name + "/themes/mataroa/layouts/_default/list.html", hugo_list
        )
        export_archive.writestr(
            export_name + "/themes/mataroa/layouts/_default/baseof.html",
            hugo_baseof,
        )
        for file_name, data in export_posts:
            export_archive.writestr(
                export_name + "/content/" + file_name, data.getvalue()
            )

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/zip")
    response["Content-Disposition"] = f"attachment; filename={export_name}.zip"
    return response


def export_unsubscribe_key(request, unsubscribe_key):
    if models.User.objects.filter(export_unsubscribe_key=unsubscribe_key).exists():
        user = models.User.objects.get(export_unsubscribe_key=unsubscribe_key)
        user.mail_export_on = False
        user.export_unsubscribe_key = uuid.uuid4()
        user.save()
        return render(
            request,
            "main/export_unsubscribe_success.html",
            {
                "blog_user": request.blog_user,
                "unsubscribed": True,
                "email": user.email,
            },
        )
    else:
        return render(
            request,
            "main/export_unsubscribe_success.html",
            {
                "blog_user": request.blog_user,
                "unsubscribed": False,
            },
        )


@login_required
def export_print(request):
    return render(
        request,
        "main/export_print.html",
        {
            "posts": models.Post.objects.filter(owner=request.user).order_by(
                "-published_at"
            ),
        },
    )


def _get_epub_titlepage(blog_user):
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE html>
<html xml:lang="en" lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{blog_user.blog_title}</title>
</head>
<body>
<h1>{blog_user.blog_title}</h1>
<p>{blog_user.blog_byline or ""}</p>
<br/>
<p>~{blog_user.username}</p>
</body>
</html>
"""


def _get_epub_chapter(post):
    chapter_body = post.body_as_html

    # convert absolute image URLs to relative paths for epub
    if "<img" in chapter_body:
        # find complete img tags
        img_pattern = re.compile(r"<img\s+([^>]+?)(/?)>")

        for img_match in img_pattern.finditer(chapter_body):
            original_tag = img_match.group(0)
            attributes = img_match.group(1)

            # extract src attribute
            src_match = re.search(r'src="([^"]+)"', attributes)
            if src_match:
                original_url = src_match.group(1)
                new_url = original_url

                # check if this URL points to our CANONICAL_HOST and has /images/ path
                if (
                    settings.CANONICAL_HOST in original_url
                    and "/images/" in original_url
                ):
                    # extract just the filename part (everything after /images/)
                    images_index = original_url.find("/images/")
                    if images_index != -1:
                        new_url = original_url[
                            images_index + 1 :
                        ]  # +1 to remove leading /

                # build new img tag with relative URL and self-closing
                new_attributes = attributes.replace(original_url, new_url)
                new_tag = f"<img {new_attributes} />"

                # replace old tag with new tag
                chapter_body = chapter_body.replace(original_tag, new_tag)

    # xhtml replacements for other self-closing tags
    chapter_body = chapter_body.replace("<br>", "<br/>").replace("<hr>", "<hr/>")

    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE html>
<html xml:lang="en" lang="en" xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
    <title>{post.title}</title>
</head>
<body>
<h2>{post.title}</h2>
{chapter_body}
</body>
</html>
"""


@require_POST
@login_required
def export_epub(request):
    # create uuid
    epub_uuid = str(uuid.uuid4())

    # load mimetype and container.xml
    with open("./export_base_epub/mimetype") as mimetype_file:
        mimetype_content = mimetype_file.read()
    with open("./export_base_epub/container.xml") as container_xml_file:
        container_xml_content = container_xml_file.read()

    # process posts
    posts = models.Post.objects.filter(owner=request.user)
    content_chapters = []
    for index, p in enumerate(posts):
        chapter = {
            "body": _get_epub_chapter(p),
            "title": p.title,
            "id": index + 1,  # +1 because we want to start from 1
            "link": f"{str(index + 1)}.xhtml",
        }
        content_chapters.append(chapter)

    # process content.opf
    content_opf_manifest = ""
    content_opf_spine = ""
    for chapter in content_chapters:
        content_opf_manifest += (
            f'    <item id="{chapter["id"]}" href="{chapter["link"]}"'
            + ' media-type="application/xhtml+xml"/>'
            + "\n"
        )
        content_opf_spine += f'    <itemref idref="{chapter["id"]}"/>' + "\n"

    # add images to manifest
    for img in models.Image.objects.filter(owner=request.user):
        # determine media type based on file extension
        if img.filename.lower().endswith((".jpg", ".jpeg")):
            media_type = "image/jpeg"
        elif img.filename.lower().endswith(".png"):
            media_type = "image/png"
        elif img.filename.lower().endswith(".gif"):
            media_type = "image/gif"
        elif img.filename.lower().endswith(".webp"):
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"  # default fallback

        content_opf_manifest += (
            f'    <item id="img-{img.slug}" href="images/{img.filename}"'
            + f' media-type="{media_type}"/>'
            + "\n"
        )

    with open("./export_base_epub/content.opf") as opf_content_file:
        content_opf_content = opf_content_file.read()

        content_opf_content = content_opf_content.replace(
            "<dc:title></dc:title>",
            f"<dc:title>{request.user.blog_title}</dc:title>",
        )
        content_opf_content = content_opf_content.replace(
            '<dc:creator opf:role="aut"></dc:creator>',
            f'<dc:creator opf:role="aut">{request.user.username}</dc:creator>',
        )
        content_opf_content = content_opf_content.replace(
            "<dc:language></dc:language>", "<dc:language>en</dc:language>"
        )
        content_opf_content = content_opf_content.replace(
            "<dc:publisher></dc:publisher>",
            f"<dc:publisher>{request.user.username}</dc:publisher>",
        )
        content_opf_content = content_opf_content.replace(
            '<dc:identifier opf:scheme="UUID"></dc:identifier>',
            f'<dc:identifier opf:scheme="UUID">{epub_uuid}</dc:identifier>',
        )
        content_opf_content = content_opf_content.replace(
            "<dc:date></dc:date>",
            f"<dc:date>{datetime.now().date().isoformat()}</dc:date>",
        )

        content_opf_content = content_opf_content.replace(
            "<!-- manifest items -->", content_opf_manifest
        )
        content_opf_content = content_opf_content.replace(
            "<!-- spine items -->", content_opf_spine
        )

    # process toc.xhtml
    toc_xhtml_body = ""
    for chapter in content_chapters:
        toc_xhtml_body += (
            f'      <li><a href="{chapter["link"]}">{chapter["title"]}</a></li>' + "\n"
        )
    with open("./export_base_epub/toc.xhtml") as toc_xhtml_file:
        toc_xhtml_content = toc_xhtml_file.read()
        toc_xhtml_content = toc_xhtml_content.replace(
            "<!-- chapters list -->", toc_xhtml_body
        )

    # process toc.ncx
    toc_ncx_body = ""
    toc_ncx_html_item = Template(
        """    <navPoint id="$chapter_id" playOrder="$chapter_playorder">
      <navLabel><text>$chapter_title</text></navLabel>
      <content src="$chapter_link"/>
    </navPoint>
"""
    )
    for chapter in content_chapters:
        new_item = toc_ncx_html_item.substitute(
            chapter_id=chapter["id"],
            chapter_playorder=chapter["id"] + 2,  # +2 because of title+toc
            chapter_title=chapter["title"],
            chapter_link=chapter["link"],
        )
        toc_ncx_body += new_item + "\n"
    with open("./export_base_epub/toc.ncx") as toc_ncx_file:
        toc_ncx_content = toc_ncx_file.read()

        toc_ncx_content = toc_ncx_content.replace(
            "<text></text>",
            f"<text>{request.user.blog_title}</text>",
        )
        toc_ncx_content = toc_ncx_content.replace(
            '<meta name="dtb:uid" content=""/>',
            f'<meta name="dtb:uid" content="{epub_uuid}"/>',
        )
        toc_ncx_content = toc_ncx_content.replace("<!-- nav points -->", toc_ncx_body)

    # create zip archive in memory
    export_name = "export-book-" + epub_uuid[:8]
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(
        zip_buffer, "a", zipfile.ZIP_DEFLATED, False
    ) as export_archive:
        # write meta pages
        export_archive.writestr("mimetype", mimetype_content)
        export_archive.writestr("META-INF/container.xml", container_xml_content)
        export_archive.writestr("OEBPS/content.opf", content_opf_content)
        export_archive.writestr("OEBPS/toc.xhtml", toc_xhtml_content)
        export_archive.writestr("OEBPS/toc.ncx", toc_ncx_content)

        # write post / chapter  files
        for chapter in content_chapters:
            export_archive.writestr(f"OEBPS/{chapter['link']}", chapter["body"])

        # write images
        for img in models.Image.objects.filter(owner=request.user):
            export_archive.writestr(f"OEBPS/images/{img.filename}", img.data)

        # write title page
        export_archive.writestr(
            "OEBPS/titlepage.xhtml", _get_epub_titlepage(request.user)
        )

    response = HttpResponse(zip_buffer.getvalue(), content_type="application/epub")
    response["Content-Disposition"] = f"attachment; filename={export_name}.epub"
    return response
