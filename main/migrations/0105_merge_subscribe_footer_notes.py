from django.db import migrations


def merge_notes(apps, schema_editor):
    User = apps.get_model("main", "User")
    for user in User.objects.all():
        subscribe = user.subscribe_note or ""
        footer = user.footer_note or ""

        # concatenate with two newlines
        if subscribe and footer:
            merged = f"{subscribe}\n\n{footer}"
        elif subscribe:
            merged = subscribe
        elif footer:
            merged = footer
        else:
            merged = ""

        user.footer_note = merged
        user.save(update_fields=["footer_note"])


def reverse_merge(apps, schema_editor):
    # can't be reversed
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0104_alter_onboard_problems_alter_onboard_quality_and_more"),
    ]

    operations = [
        migrations.RunPython(merge_notes, reverse_merge),
    ]
